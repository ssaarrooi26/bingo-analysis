import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import random
import requests
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials
import itertools

# 爬蟲測試函數
def fetch_full_table_from_web():
    # 改用第三方資料源，避免海外 IP 被台彩官網封鎖
    url = "https://lotto.auzo.tw/RK.php" 
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    try:
        # 增加 timeout 到 15 秒，並捕獲錯誤
        response = requests.get(url, headers=headers, timeout=15)
        response.encoding = 'utf-8'
        
        if response.status_code != 200:
            st.error(f"連線失敗，狀態碼：{response.status_code}")
            return None
            
        soup = BeautifulSoup(response.text, 'html.parser')
        tables = soup.find_all('table')
        all_draws = []
        
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) < 10: continue
                
                # 這裡沿用你的「特徵掃描」邏輯，非常安全
                first_cell = cells[0].get_text(strip=True)
                if not first_cell.isdigit(): continue
                
                draw_id = first_cell
                numbers = []
                for cell in cells[1:]:
                    val = cell.get_text(strip=True).lstrip('0')
                    if val.isdigit() and 1 <= int(val) <= 80:
                        numbers.append(val.zfill(2))
                
                if len(numbers) >= 20:
                    all_draws.append([draw_id] + numbers[:20])
        
        if not all_draws:
            st.warning("抓取成功但未發現符合格式的資料列")
            return None

        new_df = pd.DataFrame(all_draws)
        new_df.columns = ['期數'] + [f'num_{i}' for i in range(1, 21)]
        return new_df.set_index('期數')

    except requests.exceptions.Timeout:
        st.error("⌛ 連線逾時：第三方伺服器回應太慢，請稍後再試。")
    except Exception as e:
        st.error(f"❌ 爬蟲執行出錯: {e}")
    return None

# 新增寫入功能函數
def update_multiple_to_gsheets(new_data_list):
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        
        scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
        client = gspread.authorize(creds)
        sheet = client.open("數據分析_2026").sheet1
        
        # 抓取雲端現有期數，避免重複寫入
	# 使用 set 提高搜尋速度
        existing_ids = set(str(x).strip() for x in sheet.col_values(1) if x)
        
        rows_to_insert = []
        
        # 先對本次抓到的所有新資料做「內部去重」
        unique_batch = {}
        for draw_id, numbers in new_data_list:
            unique_batch[str(draw_id).strip()] = numbers
            
        # 排序：由大到小排 (新 -> 舊)
        # 這樣插入 index=2 時，最小的會先被塞入，最大的（最新）最後塞入，
        # reverse=True 讓最大的期號排在列表的第一個
        sorted_keys = sorted(unique_batch.keys(), key=lambda x: int(x), reverse=True)

        for draw_id in sorted_keys:
            if draw_id in existing_ids:
                continue # 已存在則跳過
            
            # 對位邏輯：81欄矩陣
            row_data = [""] * 81
            row_data[0] = draw_id
            for n in unique_batch[draw_id]:
                val = str(n).strip().lstrip('0')
                if val.isdigit():
                    num_int = int(val)
                    if 1 <= num_int <= 80:
                        row_data[num_int] = n
            
            rows_to_insert.append(row_data)
	    # 同時加入 set 防止這批新資料裡有重複期數
            existing_ids.add(draw_id)

        if not rows_to_insert:
            return "ℹ️ 官網資料已存在於雲端，無須更新。"

	# 批量插入 (使用 insert_rows，一次通訊解決所有新資料)
        # index=2 代表插入在標題列下方
        # 修正後的批量插入：使用 index=2 確保相容性

        # 4. 執行寫入 (插入在標題列 index=1 之後)
        try:
            sheet.insert_rows(rows_to_insert, index=2)
        except TypeError:
	    # 如果還是失敗，嘗試不帶參數名稱的寫法（部分舊版支援）
            sheet.insert_rows(rows_to_insert, 2)
        
        return f"✅ 成功！已批量完成 {len(rows_to_insert)} 筆數據同步。"
        
    except Exception as e:
        return f"❌ 寫入失敗: {str(e)}"

# 設定你的 Google 試算表 CSV 導出連結
SHEET_URL = "https://docs.google.com/spreadsheets/d/1n7JFERmqVCUHwpueBoCH9CKMHqjIaaEKqkDSkjjBmZM/export?format=csv"

# 設定網頁標題與圖標
st.set_page_config(page_title="Bingo 分析大師", layout="wide")

st.title("📊 Bingo Bingo 號碼趨勢隨身版")

# 讀取資料 (加上快取機制)
# ttl=5 代表每 5 秒會自動檢查一次 Google 試算表有沒有新資料
@st.cache_data(ttl=5)  # 縮短快取時間，確保同步後能即時看見結果
def load_data(url):
    # 1. 讀取 CSV
    df_raw = pd.read_csv(url, dtype=str) 

    if '期數' in df_raw.columns:
        # 2. 移除空值與空白字串
        df_raw = df_raw.dropna(subset=['期數'])
        df_raw = df_raw[df_raw['期數'].astype(str).str.strip() != ""]
        
        # 3. 轉換為數字
        df_raw['期數'] = pd.to_numeric(df_raw['期數'], errors='coerce')
        df_raw = df_raw.dropna(subset=['期數'])
        
        # 4. 去重
        df_raw = df_raw.drop_duplicates(subset=['期數'], keep='first')
        
        # 5. 【修正重點】排序：必須由大到小（新 -> 舊），這樣 iloc[0] 才是最新一期
        df_raw = df_raw.sort_values(by='期數', ascending=False).reset_index(drop=True)
        
        # 6. 格式化
        df_raw['期數'] = df_raw['pk_id'] = df_raw['期數'].astype(int).astype(str)
        
        return df_raw
    else:
        raise ValueError("CSV 格式錯誤：找不到『期數』欄位")

# --- 執行讀取與修正 ---
try:
    df = load_data(SHEET_URL)
    
    if df is not None and not df.empty:
        # --- 數據格式診斷區 (確保縮排正確) ---
        st.sidebar.subheader("🔍 數據對齊檢查")
        
        # 欄位對齊 (例如 1 -> "01")
        df.columns = [str(c).zfill(2) if str(c).isdigit() else c for c in df.columns]
        
        ball_cols = [c for c in df.columns if c.isdigit()]
        if ball_cols:
            sample_col = ball_cols[0]
            sample_val = df[sample_col].iloc[0]
            
            st.sidebar.caption(f"欄位範例: '{sample_col}' ({type(sample_col).__name__})")
            st.sidebar.caption(f"內容範例: '{sample_val}' ({type(sample_val).__name__})")

            # 強制數值化
            for col in ball_cols:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        
        # --- 顯示成功資訊 ---
        st.sidebar.success(f"✅ 同步成功！共 {len(df)} 期")
        
        # 正確顯示：首筆是最新
        st.sidebar.write(f"🚀 最新期數 (首筆)：{df['期數'].iloc[0]}")
        st.sidebar.write(f"📅 最舊期數 (末筆)：{df['期數'].iloc[-1]}")
    else:
        st.error("⚠️ 雲端資料庫目前是空的。")
        st.stop()
except Exception as e:
    st.error(f"❌ 讀取失敗: {e}")
    st.stop() 

def get_interval_stats(df):
    """
    計算區間熱力統計 (統一為 20 期平均模式)
    """
    import pandas as pd
    intervals = ["01-10", "11-20", "21-30", "31-40", "41-50", "51-60", "61-70", "71-80"]
    stats = {intv: 0 for intv in intervals}
    
    # 🚀 修正 1：確保直接使用傳入的資料（不再 head 避免二次位移）
    # 建議在外部呼叫時就給它正確的 20 期：get_interval_stats(history_df.head(20))
    analysis_df = df.head(20) 
    
    # 🚀 修正 2：統一標題格式
    ball_cols = [c for c in df.columns if str(c).strip().isdigit()]
    
    for _, row in analysis_df.iterrows():
        for col in ball_cols:
            val = pd.to_numeric(row[col], errors='coerce')
            # 🚀 修正 3：判斷邏輯。如果該格值 >= 1，代表「該標題號碼」有開出
            if pd.notnull(val) and val >= 1:
                # 號碼應該是「標題名稱」，而不是儲存格裡的值
                num = int(col) 
                idx = (num - 1) // 10
                if 0 <= idx < len(intervals):
                    stats[intervals[idx]] += 1
                        
    # 計算 20 期平均值
    for key in stats:
        stats[key] = round(stats[key] / 20.0, 4)
        
    return stats

# 遺漏期數統計
def calculate_omission(df, target_numbers=None):
    # 1. 確保欄位名稱補零 (01, 02...)
    if target_numbers is None:
        target_numbers = [str(i).zfill(2) for i in range(1, 81) if str(i).zfill(2) in df.columns]
    
    omission_dict = {}
    
    # 2. 確保最新在最上面
    df_sorted = df.sort_values(by='期數', ascending=False).reset_index(drop=True)
    
    for num in target_numbers:
        # --- 關鍵修正處 ---
        # 原本是 .notnull()，改為判定數值是否為 1
        # 先確保該欄位是數字型別，再找值為 1 的索引
        has_appeared = df_sorted[pd.to_numeric(df_sorted[num], errors='coerce') > 0].index
        
        if not has_appeared.empty:
            # 第一個出現的位置索引即為遺漏期數
            omission_dict[num] = int(has_appeared[0])
        else:
            # 如果整張表都沒出現過，設為資料總長度
            omission_dict[num] = len(df_sorted)
            
    return omission_dict

def backtest_calibration(df):
    """
    透過回溯測試 (Backtesting) 自動計算最優權重
    """
    if len(df) < 20:
        return None
    
    ball_cols = [c for c in df.columns if str(c).isdigit()]
    test_depth = 15  # 回溯測試最近 15 期
    
    # 統計指標
    stats = {
        'neighbor_hit_rate': 0.0,  # 鄰居球熱度
        'repeat_hit_rate': 0.0,    # 連莊球熱度
        'jump_hit_rate': 0.0       # 亂號(跳號)程度
    }
    
    for i in range(test_depth):
        # T0: 當前期, T1: 上一期
        t0_set = set([n for n in ball_cols if pd.to_numeric(df.iloc[i][n], errors='coerce') >= 1])
        t1_set = set([n for n in ball_cols if pd.to_numeric(df.iloc[i+1][n], errors='coerce') >= 1])
        
        # 1. 計算鄰居球規律
        t1_neighbors = set()
        for n in t1_set:
            n_int = int(n)
            if n_int > 1: t1_neighbors.add(str(n_int-1).zfill(2))
            if n_int < 80: t1_neighbors.add(str(n_int+1).zfill(2))
        
        n_hits = len(t0_set.intersection(t1_neighbors))
        stats['neighbor_hit_rate'] += n_hits
        
        # 2. 計算連莊球規律
        r_hits = len(t0_set.intersection(t1_set))
        stats['repeat_hit_rate'] += r_hits

    # 計算平均值
    avg_n = stats['neighbor_hit_rate'] / test_depth
    avg_r = stats['repeat_hit_rate'] / test_depth
    
    # --- 權重推算邏輯 ---
    # 基礎權重架構
    rec = {'neighbor': 4.5, 'trend': 3.5, 'flow': 2.0, 'omit': 2.5}
    
    # A. 鄰居權重校準：如果鄰居球平均每期開出超過 4 顆，視為強鄰居盤
    if avg_n >= 4.5:
        rec['neighbor'] = round(min(15.0, 4.5 + (avg_n * 1.8)), 1)
        rec['flow'] = 1.5  # 強規律時，降低能量回流的隨機性
    elif avg_n <= 2.5:
        rec['neighbor'] = 3.0 # 盤勢散亂，降低鄰居參考
        
    # B. 趨勢(連莊)權重校準：如果連莊球平均超過 5 顆，視為熱號盤
    if avg_r >= 5.5:
        rec['trend'] = round(min(12.0, 3.5 + (avg_r * 1.5)), 1)
        rec['omit'] = 1.5 # 熱號盤時，遺漏值參考價值會被稀釋
    elif avg_r <= 3.0:
        rec['trend'] = 2.5
        rec['omit'] = 4.5 # 連莊少時，改為追蹤遺漏反彈
        
    # C. 特殊修正：若兩者皆冷 (混亂盤)
    if avg_n < 3.0 and avg_r < 3.5:
        rec['flow'] = 5.0  # 提高能量回流(補位)
        rec['omit'] = 6.0  # 提高遺漏節奏

    return rec
    
def smart_pick_3(df, omissions, interval_stats, latest_draw_id, weights=None, enable_defense=False):
    import random
    import pandas as pd
    import streamlit as st
    
    # --- 權重初始化 ---
    if weights is None:
        weights = {'neighbor': 4.5, 'flow': 4.0, 'trend': 3.5, 'omit': 2.5}
    
    # 1. 初始化 Session State
    if 'pick_history' not in st.session_state:
        st.session_state.pick_history = {}
        
    ball_cols = [c for c in df.columns if str(c).isdigit()]
    
    # --- ⚡ 追加功能：限制分析視野為 150 期 (防止斷層偏差) ---
    analysis_window = 150
    valid_df = df.head(analysis_window)
    
    # --- 關鍵修正：精準取得上期號碼 (必須大於等於 1) ---
    last_draw_row = valid_df.iloc[0] # 使用限制後的資料集
    last_draw_nums = []
    for n in ball_cols:
        val = pd.to_numeric(last_draw_row[n], errors='coerce')
        if pd.notnull(val) and val >= 1:
            last_draw_nums.append(str(n).zfill(2))
    
    # 初始化評分表 (01-80)
    scores = {str(i).zfill(2): 0.0 for i in range(1, 81)}
    
    # --- 維度一：鄰居與連動 ---
    # 鄰居加分
    for num in last_draw_nums:
        try:
            n_int = int(num)
            for diff in [-1, 1]:
                target_n = n_int + diff
                if 1 <= target_n <= 80:
                    nb = str(target_n).zfill(2)
                    if nb in scores:
                        w_nb = weights['neighbor'] if not enable_defense else weights['neighbor'] * 0.6
                        scores[nb] += w_nb
        except:
            continue

    # 連動響應 (分析最近 50 期，已包含在 150 期安全區內)
    limit = min(len(valid_df)-1, 50) 
    for i in range(limit):
        hist_row = valid_df.iloc[i+1]
        # 取得該歷史期數開出的號碼
        hist_nums = [n for n in ball_cols if pd.to_numeric(hist_row[n], errors='coerce') >= 1]
        hist_set = set([str(n).zfill(2) for n in hist_nums])
        
        # 若與最新一期有交集 (連動關係)
        if hist_set.intersection(set(last_draw_nums)):
            weight = weights['trend'] if i < 10 else 1.0
            # 該期的「前一期」(即第 i 期) 開出的號碼視為潛力拖牌
            potential_row = valid_df.iloc[i]
            for n in ball_cols:
                if pd.to_numeric(potential_row[n], errors='coerce') >= 1:
                    n_str = str(n).zfill(2)
                    if n_str in scores:
                        scores[n_str] += weight

    # --- 維度二：遺漏節奏 ---
    # --- ⚡ 追加功能：重新計算 150 期內的「短程遺漏」，確保與全域排名一致 ---
    short_omissions = {}
    for i in range(1, 81):
        n_str = str(i).zfill(2)
        m_count = 0
        for _, row in valid_df.iterrows():
            d = [str(c).zfill(2) for c in ball_cols if pd.to_numeric(row[c], errors='coerce') >= 1]
            if n_str in d: break
            m_count += 1
        short_omissions[n_str] = m_count

    for num, o in short_omissions.items(): # 使用修正後的遺漏值
        n_str = str(num).zfill(2)
        if n_str in scores:
            # 針對熱門遺漏值加分 (3, 5, 8, 12 是常見的回補週期)
            if o in [3, 5, 8, 12]: 
                scores[n_str] += weights['omit']
            
            # 剛開出的號碼給予降溫扣分
            if o == 0: 
                scores[n_str] -= 10.0 if enable_defense else 3.0

    # --- 維度三：區間熱力 ---
    # 略過複雜的 interval_stats 判定，直接根據最新一期計算
    section_counts = {}
    for i in range(0, 80, 10):
        start, end = i + 1, i + 10
        count = sum(1 for n in last_draw_nums if start <= int(n) <= end)
        label = f"{start}-{end}"
        section_counts[label] = count

        if not enable_defense:
            # 進攻：追熱 (該區開超過 4 顆就繼續加分)
            if count >= 4:
                for n_in_zone in range(start, end + 1):
                    scores[str(n_in_zone).zfill(2)] += 1.5
        else:
            # 防守：避熱 (該區太熱就大扣分)
            if count >= 5:
                for n_in_zone in range(start, end + 1):
                    scores[str(n_in_zone).zfill(2)] -= 15.0

    # --- 維度四：推薦歷史衰減 ---
    if enable_defense:
        for num in scores:
            decay_count = st.session_state.pick_history.get(num, 0)
            if decay_count >= 1: 
                scores[num] -= (decay_count * 5.0)

    # --- 3. 排序與輸出 ---
    scored_candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    
    # 排除上期已開出的 20 碼 (避免連莊機率過低)
    final_candidates = [n[0] for n in scored_candidates if n[0] not in last_draw_nums]
    
    # 保險機制：如果過濾完沒號碼，就直接取最高分的前三個
    if not final_candidates:
        top_3 = [n[0] for n in scored_candidates[:3]]
    else:
        top_3 = final_candidates[:3]
    
    # 更新歷史紀錄 (僅紀錄推薦成功的號碼)
    if enable_defense:
        # 先將所有歷史紀錄遞減或清除，只保留當前的
        new_history = {}
        for num in top_3:
            new_history[num] = st.session_state.pick_history.get(num, 0) + 1
        st.session_state.pick_history = new_history
            
    return top_3, scores

def get_global_ranking(df, omissions, interval_stats, weights):
    import pandas as pd
    import streamlit as st  # 🚀 確保函式內部能抓到 st
    
    # 1. 鎖定分析視野 (確保不論外部傳什麼，內部只看前 150 筆)
    analysis_window = 150
    valid_df = df.head(analysis_window).copy() 

    #st.sidebar.write(f"當前計算基準: {valid_df.iloc[0].get('期數', 'n/a')}")
    # 2. 定義球號標題 (針對你改好的 01-80 格式)
    # 使用 sorted 確保標題順序在任何環境下都一致
    ball_cols = sorted([str(c).zfill(2) for c in df.columns if str(c).strip().isdigit()])
    
    # 3. 取得「基準期」開獎號碼 (用於計算鄰居球)
    # 關鍵：這是回測與即時介面最容易產生落差的地方
    last_draw_row = valid_df.iloc[0] 
    last_draw_nums = set()
    for col in df.columns:
        if str(col).strip().isdigit():
            val = pd.to_numeric(last_draw_row[col], errors='coerce')
            if val >= 1:
                last_draw_nums.add(str(col).zfill(2))
    
    # 4. 重新計算「150期內遺漏值」
    short_omissions = {}
    for i in range(1, 81):
        num_str = str(i).zfill(2)
        miss_count = 0
        for _, row in valid_df.iterrows():
            # 建立當期開獎集合
            draw = [str(c).zfill(2) for c in df.columns if str(c).strip().isdigit() and pd.to_numeric(row[c], errors='coerce') >= 1]
            if num_str in draw:
                break
            miss_count += 1
        short_omissions[num_str] = miss_count

    # 5. 計算「50期頻率微擾」
    recent_50_df = valid_df.head(50)
    freq_map = {}
    for _, row in recent_50_df.iterrows():
        draw = [str(c).zfill(2) for c in df.columns if str(c).strip().isdigit() and pd.to_numeric(row[c], errors='coerce') >= 1]
        for n in draw:
            freq_map[n] = freq_map.get(n, 0) + 1

    analysis_data = []
    
    # 6. 核心評分循環
    for i in range(1, 81):
        num_str = str(i).zfill(2)
        num_int = int(i)
        
        # A. 遺漏分
        omit_val = short_omissions.get(num_str, 0)
        s_omit = omit_val * weights['omit']
        
        # B. 動態連動分 (鄰居球)
        neighbors = {str(num_int-1).zfill(2), str(num_int+1).zfill(2)}
        hit_neighbors = len(neighbors.intersection(last_draw_nums))
        s_neighbor = hit_neighbors * weights['neighbor'] * 2 
        
        # C. 區間趨勢分 (由外部傳入的 20期平均字典)
        interval_idx = (num_int - 1) // 10
        interval_keys = ["01-10", "11-20", "21-30", "31-40", "41-50", "51-60", "61-70", "71-80"]
        current_key = interval_keys[interval_idx]
        s_trend = interval_stats.get(current_key, 0) * weights['trend']

        # 🚀 [關鍵 DEBUG] 檢查為什麼 Tab 4 是 0 分
        raw_interval_val = interval_stats.get(current_key, 0)
        s_trend = raw_interval_val * weights['trend']		
        
        # D. 微擾動 (打破平手)
        occ_count = freq_map.get(num_str, 0)
        s_bias = (occ_count / 50.0) * 0.1
        
        total_score = s_omit + s_neighbor + s_trend + s_bias


		# 🐞 這裡是除錯重點：印出特定號碼的分數組成
        # 假設你記下的 11-13 名號碼分別是 '05', '22', '38'
        # 🚀 [修正] 下面這幾行的縮排必須絕對統一，不能混用 Tab
        #target_debug = ['71', '47', '35'] 
        #if num_str in target_debug:
            # 增加 raw_interval_val 的顯示，一眼看出是不是字典傳輸失敗
            #st.write(f"🔍 號碼 {num_str} ({current_key})：總分={round(total_score,4)} 遺漏:{round(s_omit,2)} | 鄰居:{round(s_neighbor,2)} | 趨勢原值={raw_interval_val} | 趨勢得分={round(s_trend,2)} | 微擾:{round(s_bias,4)}")
			
        analysis_data.append({
            "號碼": num_str,
            "總得分": round(total_score, 4), 
            "連動": "🔥" if hit_neighbors > 0 else " ",
            "150期遺漏": omit_val,
            "得分佔比": 0 
        })
    
    # 7. 穩定排序：先比總分(大到小)，再比號碼(小到大)
    rank_df = pd.DataFrame(analysis_data).sort_values(
        by=["總得分", "號碼"], 
        ascending=[False, True] 
    ).reset_index(drop=True)
    
    # 補算佔比
    total_sum = rank_df["總得分"].sum()
    if total_sum > 0:
        rank_df["得分佔比"] = (rank_df["總得分"] / total_sum * 100).round(2).astype(str) + "%"

    rank_df.index += 1 
    return rank_df

def smart_pick_3_backtest(df, omissions, interval_stats, weights={}):
    """
    回測專用選號邏輯：排除 Session State，支援外部權重傳入。
    """
    import pandas as pd
    
    # 提取權重參數
    w_neighbor = weights.get('neighbor', 4.5)
    w_flow = weights.get('flow', 4.0) # 同步強化
    w_trend = weights.get('trend', 3.5)
    w_omit = weights.get('omit', 2.5)

    ball_cols = [c for c in df.columns if str(c).isdigit()]
    last_draw_row = df.iloc[0]
    last_draw_nums = [n for n in last_draw_row.index if n in ball_cols and last_draw_row.notnull()[n]]
    
    # 初始化評分表
    scores = {str(i).zfill(2): 0.0 for i in range(1, 81)}

    # --- 維度一：鄰居與爆發力 (對齊 smart_pick_3 邏輯) ---
    for num in last_draw_nums:
        n_int = int(num)
        for diff in [-1, 1]:
            nb = str(n_int + diff).zfill(2)
            if nb in scores:
                scores[nb] += w_neighbor # 改回無差別加分

    # --- 維度二：區間飽和度與能量回流 (強化扣分感) ---
    zone_cols = [c for c in interval_stats.columns if '-' in str(c)]
    if zone_cols:
        for z in zone_cols:
            try:
                start, end = map(int, z.split('-'))
                count = sum(1 for n in last_draw_nums if start <= int(n) <= end)
                if count >= 4: 
                    for i in range(start, end + 1):
                        scores[str(i).zfill(2)] -= 8.0 # 強化回測時的規避感
                    adj_low, adj_high = str(start-1).zfill(2), str(end+1).zfill(2)
                    if adj_low in scores: scores[adj_low] += w_flow
                    if adj_high in scores: scores[adj_high] += w_flow
            except: continue

    # --- 維度三：短期連動與趨勢 ---
    for i in range(min(len(df)-1, 50)):
        current_set = set([n for n in df.iloc[i+1].index if n in ball_cols and df.iloc[i+1].notnull()[n]])
        next_gen_nums = [n for n in df.iloc[i].index if n in ball_cols and df.iloc[i].notnull()[n]]
        if current_set.intersection(set(last_draw_nums)):
            weight = w_trend if i < 10 else 1.0 
            for num in next_gen_nums:
                if num in scores: scores[num] += weight

    # --- 維度四：遺漏節奏 ---
    for num, o in omissions.items():
        if num in scores:
            if o in [3, 5, 8, 13]: scores[num] += w_omit
            if o == 0: scores[num] -= 6.0

    scored_candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    final_candidates = [n[0] for n in scored_candidates if n[0] not in last_draw_nums]
    
    return final_candidates[:3]

def run_backtest(df, base_weights, use_ai):
    import pandas as pd
    import numpy as np

    # --- 參數設定 ---
    test_range = 50   
    window = 1        # ⚡ 修改：從 5 改為 1，改為「直擊模式」，僅驗證建議號碼在下一期的表現
    results = []
    ball_cols = [c for c in df.columns if str(c).isdigit()]
    
    for i in range(window, test_range + window):
        if i + 50 >= len(df): 
            break 
        
        # 模擬當時時間點
        current_df = df.iloc[i:]           
        # ⚡ 修改：只取開獎序列中「緊接在歷史之後」的那一期
        actual_next_draw = df.iloc[i-1] 
        
        # --- 🕵️ Step 1: 盤勢偵測 (分析最近 10 期規律) ---
        recent_10 = current_df.head(10)
        neighbor_hits = 0
        for idx in range(len(recent_10)-1):
            curr_row = recent_10.iloc[idx][ball_cols]
            curr_draw = set([str(c).zfill(2) for c in ball_cols if pd.to_numeric(curr_row[c], errors='coerce') >= 1])
            prev_row = recent_10.iloc[idx+1][ball_cols]
            prev_draw = set([str(p).zfill(2) for p in ball_cols if pd.to_numeric(prev_row[p], errors='coerce') >= 1])
            neighbor_hits += len([n for n in curr_draw if any(abs(int(n)-int(p)) == 1 for p in prev_draw)])
        
        avg_neighbor = neighbor_hits / 10
        
        # --- ⚙️ Step 2: 權重決策邏輯 ---
        dynamic_weights = base_weights.copy()
        strategy_mode = "手動配置"
        
        if avg_neighbor > 4.5:
            trend_type = "🔥 熱門連動盤"
            confidence = "高"
            ai_template = {'neighbor': 8.5, 'omit': 1.0, 'trend': 5.5, 'flow': 2.0}
        elif avg_neighbor < 2.2:
            trend_type = "❄️ 冷號回補盤"
            confidence = "中"
            ai_template = {'neighbor': 1.2, 'omit': 9.5, 'trend': 1.0, 'flow': 3.0}
        else:
            trend_type = "⚖️ 標準平衡盤"
            confidence = "低"
            ai_template = {'neighbor': 4.0, 'omit': 4.0, 'trend': 4.0, 'flow': 4.0}

        if use_ai:
            dynamic_weights = ai_template
            strategy_mode = "AI 自動校準"

        # --- 🎯 Step 3: 選號與驗證 ---
        omissions = calculate_omission(current_df, ball_cols) 
        interval_stats = get_interval_stats(current_df)
        recs, _ = smart_pick_3(current_df, omissions, interval_stats, None, weights=dynamic_weights)
        recs_set = set([str(n).zfill(2) for n in recs])
        
        # --- 📊 Step 4: 下一期命中檢驗 (單期驗證) ---
        # ⚡ 修改：直接取得該期號碼，不再使用迴圈跑 5 期
        draw = [str(c).zfill(2) for c in ball_cols if pd.to_numeric(actual_next_draw[c], errors='coerce') >= 1]
        hits = recs_set.intersection(set(draw))
        hit_count = len(hits) # ⚡ 修改：單期命中數
        winning_nums = list(hits)
        
        # --- 📝 Step 4: 產出報告 ---
        results.append({
            "期數": df.index[i-1],           # ⚡ 修改：顯示被預測的那一期期號
            "建議號碼": ", ".join(recs),
            "命中號碼": ", ".join(winning_nums) if winning_nums else "無",
            "最高單期命中": hit_count,              # ⚡ 修改：欄位由「最高單期命中」改為直觀的「命中數」
            "策略模式": strategy_mode,      
            "偵測盤勢": trend_type,
            "最終權重(鄰/趨/流/遺)": f"{dynamic_weights['neighbor']}/{dynamic_weights['trend']}/{dynamic_weights['flow']}/{dynamic_weights['omit']}",
            "信心指數": confidence,
            "三星成功": 1 if hit_count == 3 else 0,
            "二星命中": 1 if hit_count == 2 else 0,
            "一星命中": 1 if hit_count == 1 else 0
        })
        
    return pd.DataFrame(results)

def run_backtest_rank_11_13(df, base_weights, use_ai, start_r=11, end_r=13):
    import pandas as pd
    results = [] # 初始化空清單，用來儲存每一期回測的比對結果
    
    # 自動偵測 CSV 中的期號欄位名稱（相容不同格式的標題）
    id_col = next((c for c in ['期號', '期數', 'DrawNo'] if c in df.columns), None)
    # 擷取所有純數字標題的欄位（即代表 01-80 號碼球的數據列）
    ball_cols = [c for c in df.columns if str(c).isdigit()]
    
    test_range = 50 # 設定往回追蹤的回測總期數
    for i in range(0, test_range):
        # --- 模擬當時時間軸 ---
        # i=0 代表最新一期，i=1 代表前一期。target_row 是我們要驗證「預測是否命中」的那一期。
        target_row = df.iloc[i]          
        # history_df 模擬當時開獎後的「歷史資料視野」，取該期之後的 150 期，確保不偷看未來數據
        history_df = df.iloc[i+1 : i+151] 
        
        # 如果剩餘的歷史資料不足 150 期，則無法進行精準評分，跳出迴圈
        if len(history_df) < 150: break 
        
        # 1. 取得基礎權重
        # 複製側邊欄傳入的權重設定，避免迴圈內的修改影響到原始變數
        test_weights = base_weights.copy()

        # 2. ⚡ 關鍵同步：取得當時的區間熱力數據
        # 呼叫外部統一的 get_interval_stats 函式，傳入模擬的歷史視窗
        # 這裡會得到一個「字典」格式的 20 期平均熱度，與 Tab 1 即時顯示完全一致
		# 這樣可以確保傳進去的就是從基準期開始算的 20 期
        temp_interval_stats = get_interval_stats(history_df.head(20))

        # 3. 🔍 執行核心排名計算
        # 將當時的歷史、空遺漏表、統計字典、權重傳入排名引擎
        # 引擎內部會自動計算：遺漏分、連動分、趨勢分，以及「微擾動係數」
        rank_df = get_global_ranking(history_df, {}, temp_interval_stats, test_weights)
        
        # 4. 🎯 精準擷取排名部位
        if not rank_df.empty:
            try:
                # 直接利用 get_global_ranking 已經排好序（總得分 + 號碼順序）的結果
                # 使用 iloc 進行切片。例如 start_r=11, end_r=13，會取 index 10 到 12 的資料
                picked_nums = rank_df.iloc[start_r-1 : end_r]["號碼"].tolist()
            except:
                # 若發生索引越界（例如號碼不足）則跳過此期
                continue
        else:
            continue
            
        # 5. 驗證中獎情況
        # 將建議號碼補零至兩位數格式，建立集合（Set）以便進行交集運算
        recs_set = set([str(n).zfill(2) for n in picked_nums])
        # 找出 target_row 中數值 >= 1 的欄位，即為該期實際開出的號碼
        draw_nums = [str(c).zfill(2) for c in ball_cols if pd.to_numeric(target_row[c], errors='coerce') >= 1]
        # 使用 set.intersection 取得「命中號碼」
        hits = recs_set.intersection(set(draw_nums))
        hit_count = len(hits) # 計算總命中顆數
        
        # 6. 取得期號用於報表顯示
        display_period = target_row[id_col] if id_col else df.index[i]

        # 將此期回測的所有詳情與命中指標封裝進字典
        results.append({
            "回測序號": f"#{str(i+1).zfill(2)}", 
            "原始期號": display_period,
            f"建議號碼({start_r}-{end_r})": ", ".join(picked_nums), # 動態顯示當前測試的排名區間
            "命中詳情": ", ".join(list(hits)) if hits else "無",
            "最高單期命中": hit_count,
            "三星成功": 1 if hit_count == 3 else 0, # 若命中 3 顆則標註為三星成功
            "二星命中": 1 if hit_count == 2 else 0,
            "一星命中": 1 if hit_count == 1 else 0
        })
        
    # 將所有期數的結果轉換為 DataFrame 格式回傳，方便 Streamlit 渲染表格與染色
    return pd.DataFrame(results)

def analyze_group_performance(df, current_weights):
    """
    【嚴謹對齊版】自動測試 3-13 名中，每三碼為一組的歷史勝率。
    完全呼叫既有的 get_global_ranking 確保邏輯與回測 100% 一致。
    """
    # 1. 準備存儲容器
    # 組別定義：3-5, 4-6, 5-7, 6-8, 7-9, 8-10, 9-11, 10-12, 11-13
    groups = {f"{i}-{i+2}": {"3星": 0, "2星": 0, "1星": 0, "0星": 0} for i in range(3, 12)}
    
    analysis_history = []
    progress_bar = st.progress(0)
    status_text = st.empty()

    # 2. 模擬 50 期回測 (這是最耗時但最準確的部分)
    for i in range(0, 50):
        # --- 嚴謹切片：這部分與你的回測邏輯完全一致 ---
        history_df = df.iloc[i+1 : i+151]
        actual_row = df.iloc[i]
        actual_draw = [str(c).zfill(2) for c in df.columns if str(c).strip().isdigit() and pd.to_numeric(actual_row[c], errors='coerce') >= 1]
        
        # 呼叫你校正過的趨勢統計
        temp_interval_stats = get_interval_stats(history_df.head(20))
        
        # 呼叫核心排名方法 (確保邏輯對齊)
        rank_df = get_global_ranking(history_df, {}, temp_interval_stats, current_weights)
        
        # 紀錄這一期的結果
        analysis_history.append((set(actual_draw), rank_df))
        
        status_text.write(f"正在同步回測數據... 第 {i+1}/50 期")
        progress_bar.progress((i + 1) / 50)

    # 3. 交叉比對各組別勝率
    final_stats = []
    for group_name, counts in groups.items():
        start_idx = int(group_name.split('-')[0]) - 1 # 轉為 dataframe index
        
        for draw_set, rank_df in analysis_history:
            # 精準取出該組的三個號碼
            group_nums = rank_df.iloc[start_idx : start_idx + 3]["號碼"].tolist()
            hits = len(draw_set.intersection(group_nums))
            
            if hits == 3: counts["3星"] += 1
            elif hits == 2: counts["2星"] += 1
            elif hits == 1: counts["1星"] += 1
            else: counts["0星"] += 1
            
        # 計算綜合權重分 (例如：3星給10分, 2星給3分, 1星給1分)
        score = (counts["3星"] * 10) + (counts["2星"] * 3) + (counts["1星"] * 1)
        
        final_stats.append({
            "名次組別": f"第 {group_name} 名",
            "3星次數": counts["3星"],
            "2星次數": counts["2星"],
            "1星次數": counts["1星"],
            "三星勝率": f"{(counts['3星']/50)*100:.1f}%",
            "綜合評分": score
        })

    # 4. 產出排序後的結果
    result_df = pd.DataFrame(final_stats).sort_values(by="綜合評分", ascending=False)
    
    status_text.success("✅ 數據對齊分析完成！")
    return result_df

def dual_dimension_analysis(df):
    if len(df) < 20:
        return None, "數據量不足以進行雙維度分析"

    # 定義視窗
    micro_window = df.tail(10)
    macro_window = df.tail(100) if len(df) >= 100 else df

    def get_stats(target_df):
        all_draws = []
        for _, row in target_df.iterrows():
            draw = [int(col) for col in target_df.columns if col.isdigit() and row[col] != "" and not pd.isna(row[col])]
            all_draws.append(set(draw))
        
        repeats, neighbors = [], []
        for i in range(1, len(all_draws)):
            repeats.append(len(all_draws[i].intersection(all_draws[i-1])))
            prev_n = {n + d for n in all_draws[i-1] for d in [-1, 1] if 1 <= n + d <= 80}
            neighbors.append(len(all_draws[i].intersection(prev_n)))
        
        return sum(repeats)/len(repeats), sum(neighbors)/len(neighbors)

    micro_rep, micro_nei = get_stats(micro_window)
    macro_rep, macro_nei = get_stats(macro_window)

    # 權重建議邏輯
    rec = {'neighbor': 4.5, 'trend': 3.5, 'flow': 2.0, 'omit': 2.5, 'tips': []}

    # 1. 微觀診斷：決定鄰居與短期趨勢
    if micro_nei > macro_nei * 1.2:
        rec['neighbor'] = 6.0
        rec['tips'].append("⚡ 短期鄰居竄升：目前處於『區塊集結』盤勢。")
    
    if micro_rep > macro_rep * 1.3:
        rec['trend'] = 5.5
        rec['tips'].append("🔥 短期連莊過熱：強勢號碼正在連發。")

    # 2. 宏觀診斷：決定能量回流 (如果長期連莊低，代表號碼輪轉快)
    if macro_rep < 1.5:
        rec['flow'] = 4.0
        rec['tips'].append("🌊 宏觀能量回補：冷門號回歸機率增高。")

    return rec, micro_rep, micro_nei, macro_rep, macro_nei

# 2. 側邊欄：設定參數
st.sidebar.header("🚀 數據同步工具")
if st.sidebar.button("🔄 批量同步至雲端"):
    with st.sidebar:
        with st.spinner("正在執行自動化流程..."):
            # A. 抓取官網表格 (回傳 DataFrame, index=期數)
            web_df = fetch_full_table_from_web()
            
            if web_df is not None and not web_df.empty:
                # 準備要交給 Google Sheets 的格式：[(期數, [號碼列表])]
                sync_list = []
                for draw_id, row in web_df.iterrows():
                    # 將該列所有數值轉為字串並放入列表
                    numbers = [str(n) for n in row.values]
                    sync_list.append((str(draw_id), numbers))
                
                # B. 呼叫批量寫入函數
                write_msg = update_multiple_to_gsheets(sync_list)
                st.write(write_msg)
                
                # C. 成功後的刷新機制
                if "成功" in write_msg:
                    st.cache_data.clear()
                    st.success("數據已刷新，請查看報表")
                    st.rerun()
            else:
                st.error("❌ 無法取得官網資料，請檢查網路連線")

st.sidebar.divider() # 加入分隔線，區分自動化與原本的設定

st.sidebar.header("設定選項")
group_size = st.sidebar.slider("區間期數 (每幾期一組)", 1, 20, 5)
target_numbers = [str(i) for i in range(1, 81)]
existing_cols = [col for col in target_numbers if col in df.columns and col != '期數']

st.sidebar.divider() # 加入分隔線


st.sidebar.header("🎯 建議權重控制")

rec, mi_r, mi_n, ma_r, ma_n = dual_dimension_analysis(df)
calibrated_rec = backtest_calibration(df)

# 1. 初始化 session_state (這段放在最前面，確保不會報錯)
DEFAULT_WEIGHTS = {
    'neighbor': 4.5,
    'trend': 3.5,
    'flow': 2.0,
    'omit': 2.5
}

# 確保 key 已經存在於 session_state 中
for k, v in DEFAULT_WEIGHTS.items():
    s_key = f"val_{k}" # 我們用 val_ 作為儲存數值的 key
    if s_key not in st.session_state:
        st.session_state[s_key] = v

# 2. 模式開關
is_defensive = st.sidebar.toggle("🛡️ 啟用風險規避模式", value=False)

# 3. 智慧校準與恢復按鈕 (修改這裡的賦值邏輯)
col_btn1, col_btn2 = st.sidebar.columns(2)

if col_btn1.button("🔄 恢復預設"):
    for k, v in DEFAULT_WEIGHTS.items():
        st.session_state[f"val_{k}"] = v
    st.rerun()

# 假設 trend_rec 是從你的診斷系統產生的
if col_btn2.button("🪄 智慧校準"):
    if calibrated_rec:
        # 將運算結果存入 session_state
        st.session_state["val_neighbor"] = calibrated_rec['neighbor']
        st.session_state["val_trend"] = calibrated_rec['trend']
        st.session_state["val_flow"] = calibrated_rec['flow']
        st.session_state["val_omit"] = calibrated_rec['omit']
        
        st.sidebar.success(f"已根據最近 15 期盤勢完成優化！")
        st.rerun()
    else:
        st.sidebar.error("數據量不足，無法校準")

# 4. 數值輸入框 (關鍵：不要在元件上直接設定與儲存變數同名的 key)
sw_n = st.sidebar.number_input("鄰居觸發", min_value=1.0, max_value=20.0, 
                               value=st.session_state["val_neighbor"], step=0.1)
sw_t = st.sidebar.number_input("短期連動", min_value=1.0, max_value=20.0, 
                               value=st.session_state["val_trend"], step=0.1)
sw_f = st.sidebar.number_input("能量回流", min_value=0.0, max_value=10.0, 
                               value=st.session_state["val_flow"], step=0.1)
sw_o = st.sidebar.number_input("遺漏節奏", min_value=1.0, max_value=10.0, 
                               value=st.session_state["val_omit"], step=0.1)

# 同步回 session_state (確保手動輸入也會被記住)
st.session_state["val_neighbor"] = sw_n
st.session_state["val_trend"] = sw_t
st.session_state["val_flow"] = sw_f
st.session_state["val_omit"] = sw_o

# 5. 組合成函數使用的字典
sidebar_weights = {
    'neighbor': sw_n, 
    'trend': sw_t, 
    'flow': sw_f, 
    'omit': sw_o
}

# --- 回測控制區 ---
st.sidebar.subheader("🤖 策略控制")
use_ai_calibration = st.sidebar.checkbox(
    "開啟 AI 動態權重接管", 
    value=False, 
    help="勾選時：系統自動偵測盤勢並強制覆蓋為專家權重。未勾選：完全採用上方手動設定。"
)

#盤勢儀表板
st.sidebar.markdown("---")
st.sidebar.subheader("📊 盤勢診斷儀表板")

if rec:
    # 顯示指標對比
    col1, col2 = st.sidebar.columns(2)
    col1.metric("微觀連莊", f"{mi_r:.1f}", f"{mi_r - ma_r:+.1f}")
    col2.metric("微觀鄰居", f"{mi_n:.1f}", f"{mi_n - ma_n:+.1f}")
    
    for tip in rec['tips']:
        st.sidebar.caption(tip)
    
    if not rec['tips']:
        st.sidebar.caption("⚖️ 當前盤勢穩定，符合長期統計規律。")

    
# 3. 功能分頁
tab1, tab2, tab3, tab4 = st.tabs(["🔥 頻率分佈圖", "分段趨勢表", "🔮 智能建議", "策略回測"])

with tab1:
    st.header("1-80 號碼總出現頻率")
    frequency = df[existing_cols].notnull().sum()
    
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(frequency.index, frequency.values, color='skyblue')
    plt.xticks(rotation=90, fontsize=6)
    st.pyplot(fig)

with tab2:
    st.header(f"每 {group_size} 期趨勢分析")
    # 1. 為了讓「最小期數」是第一筆，我們先建立一個正序的副本 (舊 -> 新)
    df_ascending = df.iloc[::-1].copy().reset_index(drop=True)
    
    # 2. 分組計算：現在 index 0 是最舊的資料
    df_ascending['Group'] = (df_ascending.index // group_size) + 1
    
    # 3. 計算每個區間的出現次數
    interval_stats = df_ascending.groupby('Group')[existing_cols].apply(lambda x: x.notnull().sum())
    
    # 4. 重新定義索引名稱 (例如：第 1~5 筆)
    interval_stats.index = [
        f"第 {int((i-1)*group_size + 1)}~{int(i*group_size)} 筆" 
        for i in interval_stats.index
    ]
    
    # 2. 獲取數據的最大值，用來計算顏色比例
    max_val = interval_stats.max().max()
    if max_val <= 1: max_val = 2 # 防止除以零

    # 3. 自定義非線性色階
    # 我們設定：0 是紅色，1/max_val 的位置是白色，1 是綠色
    # 這樣 0->1 是紅變白，1->最大值 是白變綠
    nodes = [0.0, 1.0/max_val, 1.0]
    colors = ["#FF3333", "#FFFFFF", "#008000"] # 紅、白、深綠
    special_cmap = mcolors.LinearSegmentedColormap.from_list("special_rng", list(zip(nodes, colors)))
    
    # 4. 設定色階 
    # axis=None 代表對整個表格進行全域比較，而不僅是單行或單列比較
    # 這樣「全表」出現 3 次的格子顏色都會一模一樣
    styled_df = interval_stats.style.background_gradient(
        cmap=special_cmap, 
        axis=None,    # 關鍵：全域比較，相同數值必同色
        low=0,        # 設定顏色範圍的最小值
        high=0.5      # 稍微調高上限，可以讓顏色對比更明顯（可視情況調整）
    ).format("{:.0f}") # 確保顯示的是整數
    
    # 3. 顯示表格
    st.dataframe(styled_df, height=600)

with tab3:
    st.header("🔮 智能選號建議")

    # 取得當前最新一期期數
    if not df.empty:
        # 假設你的期數欄位名稱為 '期數'
        latest_draw_id = int(df['期數'].max())
        
        # 顯示最新期數資訊
        st.info(f"📅 當前最新期數：**{latest_draw_id}**")
        
        # 計算五期循環邏輯
        remainder = latest_draw_id % 5
        
        if remainder == 0:
            st.success("🎯 當前已達成 5 期循環！數據已完整，適合進行下波預測分析。")
        else:
            wait_count = 5 - remainder
            st.warning(f"⏳ 目前處於循環中：第 **{remainder}** 期")
            st.write(f"👉 距離下一個完整區間（5期）還需等待：**{wait_count}** 期")
            
        st.divider() # 分隔線
    
    # 1. 準備基礎數據
    latest_counts = df[existing_cols].notnull().sum()
    
    # 2. 演算法 A：熱門號碼
    hot_numbers = latest_counts.nlargest(10).index.tolist()
    
    # 3. 演算法 B：潛力冷號
    cold_numbers = latest_counts.nsmallest(20).index.tolist()
    # 確保隨機抽取不報錯
    sample_size = min(len(cold_numbers), 5)
    suggested_cold = random.sample(cold_numbers, sample_size)

    # 顯示建議介面
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("🔥 熱門號碼建議")
        st.write("根據近期大數據，這幾號手氣最旺：")
        st.info(", ".join(hot_numbers))
        
    with col2:
        st.subheader("❄️ 冷門回補建議")
        st.write("這幾號沉寂已久，近期可能回補：")
        st.warning(", ".join(suggested_cold))

    st.divider()

    st.subheader("📊 號碼遺漏值統計 (Omission Analysis)")
    
    # 計算遺漏值
    omissions = calculate_omission(df, existing_cols)
    
    # 轉為 DataFrame 方便顯示
    omission_df = pd.DataFrame(list(omissions.items()), columns=['球號', '遺漏期數'])
    omission_df = omission_df.sort_values(by='遺漏期數', ascending=False)
    
    col1, col2 = st.columns([1, 2])
    with col1:
        st.write("🔥 **目前最久未開號碼**")
        st.table(omission_df.head(10).reset_index(drop=True))
        
    with col2:
        st.write("📈 **遺漏分佈圖**")
        st.bar_chart(omission_df.set_index('球號')['遺漏期數'])

    # 結合 5 期循環邏輯
    if not omission_df.empty:
        max_omission_num = omission_df.iloc[0]['球號']
        max_omission_val = omission_df.iloc[0]['遺漏期數']
        st.info(f"💡 **觀察建議**：號碼 **{max_omission_num}** 已經連續 **{max_omission_val}** 期未開出了。搭配當前循環剩餘期數，可以觀察其是否會在近期反彈。")

    # --- 數據分析與視覺化區塊 ---
    if len(df) >= 2:
        last_row = df.iloc[0]  
        prev_row = df.iloc[1]  
        
        # 找出真正有開出的號碼
        current_nums = set([
            col for col in existing_cols 
            if pd.to_numeric(last_row[col], errors='coerce') >= 1
        ])
        prev_nums = set([
            col for col in existing_cols 
            if pd.to_numeric(prev_row[col], errors='coerce') >= 1
        ])
        
        # 1. 連莊號碼分析
        repeat_nums = current_nums.intersection(prev_nums)
        
        st.subheader("1. 🔄 連莊追蹤 (Repeated)")
        col_r1, col_r2 = st.columns([1, 2])
        col_r1.metric("本期連莊數", f"{len(repeat_nums)} 碼")
        
        formatted_repeats = sorted([str(n).zfill(2) for n in repeat_nums])
        col_r2.write(f"最新連莊號碼： {', '.join(formatted_repeats) if repeat_nums else '無'}")
        
        st.divider()

        # 2. 黃金區間分析
        st.subheader("2. 🏆 黃金區間 (Sections)")
        section_data = {}
        for i in range(0, 80, 10):
            start, end = i + 1, i + 10
            label = f"{start:02d}-{end:02d}"
            section_cols = [str(n).zfill(2) for n in range(start, end + 1) if str(n).zfill(2) in existing_cols]
            count = sum(pd.to_numeric(last_row[section_cols], errors='coerce') >= 1)
            section_data[label] = int(count)
            
        st.bar_chart(pd.Series(section_data), color="#f4a261")
        
        if section_data:
            max_sec = max(section_data, key=section_data.get)
            st.caption(f"💡 目前最旺區間：{max_sec} 區 (開出 {section_data[max_sec]} 碼)")

        st.divider()

        # 3. 尾數熱度分析
        st.subheader("3. 🔢 尾數分析 (Last Digit)")
        tail_data = {str(i): 0 for i in range(10)}
        for num in current_nums:
            try:
                tail = str(int(num) % 10)
                tail_data[tail] += 1
            except:
                continue
                
        tail_df = pd.DataFrame(list(tail_data.items()), columns=['尾數', '開出個數'])
        st.dataframe(
            tail_df.set_index('尾數').T.style.background_gradient(cmap="Greens", axis=1)
        )
        
        # --- 核心運算執行 ---
        try:
            draw_id_int = int(latest_draw_id)
            remainder = draw_id_int % 5
        except:
            draw_id_int = 0
            remainder = -1

        # 呼叫建議函式
        recommendations, all_scores = smart_pick_3(df, omissions, interval_stats, latest_draw_id, weights=sidebar_weights, enable_defense=is_defensive)

        # --- 當前選號模式說明 ---
        if not is_defensive:
            st.subheader("🔥 當前模式：進攻型")
            st.caption("🚀 策略重點：**鄰居強力補位**、**熱門區域追蹤**。")
        else:
            st.subheader("🛡️ 當前模式：風險規備型")
            st.caption("⚖️ 策略重點：**避開飽和區域**、**號碼疲勞降溫**。")

        st.markdown("---")

        # --- 建議號碼展示 ---
        st.subheader("🎯 高精度交叉驗證選碼")
        if not recommendations:
            st.warning("⚠️ 系統暫時無法產出建議號碼。請確認數據判定是否正確。")
        else:
            cols = st.columns(3)
            for i, num in enumerate(recommendations):
                score_val = all_scores.get(num, 0)
                cols[i].metric(label=f"建議號碼 {i+1}", value=num, delta=f"權重分: {score_val:.1f}")

        # --- 排行榜展示 ---
        st.write("---")
        st.subheader("📊 號碼潛力價值排行榜 (Top 10)")
        if all_scores:
            score_df = pd.DataFrame(list(all_scores.items()), columns=['號碼', '加權總分'])
            score_df = score_df.sort_values(by='加權總分', ascending=False).head(10).reset_index(drop=True)
            st.dataframe(score_df.style.highlight_max(axis=0, color='#ff4b4b'), use_container_width=True)
			

        # --- 系統控制 ---
        with st.expander("⚙️ 系統控制與追蹤"):
            if st.button("🔴 清空推薦歷史 (重置衰減狀態)"):
                st.session_state.pick_history = {}
                st.success("已成功重置！")
                st.rerun()
            
            if st.session_state.get('pick_history'):
                st.write("目前連續推薦紀錄：", st.session_state.pick_history)

        # --- 循環末端避熱機制說明 ---
        if remainder != -1:
            if remainder in [0, 4]:
                st.caption(f"🛡️ 目前期數 {latest_draw_id}：已啟動「循環末端避熱」機制。")
            else:
                st.caption(f"ℹ️ 目前期數 {latest_draw_id}：循環進行中。")

        # --- 綜合推薦組合 ---
        st.divider()
        st.subheader("🎲 綜合推薦組合")
        try:
            top_tail = max(tail_data, key=tail_data.get)
            st.success(f"建議關注：**{max_sec}** 區間的號碼，並優先考慮「**{top_tail}**」尾的組合。")
        except:
            st.info("綜合分析數據讀取中...")

    else:
        st.info("數據量不足，請至少輸入兩期資料以進行進階分析。")

    st.caption("註：預測邏輯基於歷史統計數據，僅供參考。請理性娛樂。")

st.info("💡 提示：手機開啟時，將此網頁「新增至主螢幕」即可像 App 一樣使用。")

st.header("📊 雙軌數據分析中心")
st.info("左側為精準組合預測，右側為 80 顆球全域熱度排行，方便你執行『逆向排除』或『手動加選』。")

col1, col2 = st.columns([1, 1.8])

with col1:
    st.subheader("🎯 方案一：Smart Pick 3")
    # 執行原本的選號邏輯
    recs, _ = smart_pick_3(df, omissions, interval_stats, None, weights=sidebar_weights)
    
    st.markdown("---")
    for r in recs:
        st.markdown(f"### 📍 推薦號碼：`{r}`")
    st.markdown("---")
    st.caption("💡 這是基於當前權重算出的最高分三位一體組合。")

with col2:
    st.subheader("📈 方案二：全號碼競爭力排行榜")
    # 🚀 關鍵修正：在執行排名前，先算出最新的 20 期趨勢字典
    # 確保傳入 get_global_ranking 的 interval_stats 是有數值的
    current_interval_stats = get_interval_stats(df.head(20)) 
    
    # 執行強化版全域排名 
    rank_df = get_global_ranking(df, omissions, current_interval_stats, sidebar_weights)
    
    # 快速摘要
    top_5 = rank_df.head(5)["號碼"].tolist()
    bottom_5 = rank_df.tail(5)["號碼"].tolist()
    
    c1, c2 = st.columns(2)
    c1.success(f"🔝 潛力前五：{', '.join(top_5)}")
    c2.error(f"🗑️ 建議避雷：{', '.join(bottom_5)}")
    
    # 顯示完整資料表
    st.dataframe(
        rank_df.style.background_gradient(subset=['總得分'], cmap='YlOrRd'),
        use_container_width=True,
        height=450
    )

with tab4: # 第四個 Tab
    st.header("📊 策略勝率回測 (過去 50 期)")

    # --- 回測專用的局部權重控制 ---
    st.info("💡 此處調整僅影響回測結果，不會改變 Tab 3 的建議號碼。")
    with st.expander("⚙️ 模擬實驗室權重微調", expanded=False):
        bw_n = st.slider("模擬-鄰居觸發", 1.0, 10.0, 4.5, key="back_n")
        bw_t = st.slider("模擬-短期連動", 1.0, 10.0, 3.5, key="back_t")
        bw_f = st.slider("模擬-能量回流", 0.0, 10.0, 4.0, key="back_f") # 建議調高上限至 10.0
        bw_o = st.slider("模擬-遺漏節奏", 1.0, 5.0, 2.5, key="back_o")

    # 封裝權重字典
    backtest_weights = {
        'neighbor': bw_n, 
        'trend': bw_t, 
        'flow': bw_f, 
        'omit': bw_o
    }

    if st.button("🚀 開始執行 50 期回測"):
	    with st.spinner("系統正在模擬歷史選號並驗證結果..."):
	        # 執行回測：確保傳入 sidebar_weights (目前側邊欄的數值)
	        backtest_df = run_backtest(df, sidebar_weights, use_ai_calibration)
	    
	    # --- 1. 錯誤檢查機制 ---
	    if backtest_df is None or backtest_df.empty:
	        st.warning("⚠️ 回測未產生任何結果，請確認數據源是否完整（建議至少需 100 期歷史數據）。")
	    else:
	        # --- 2. 符合新統計定義的數據處理 ---
	        total_tests = len(backtest_df)
	        success_3 = backtest_df["三星成功"].sum()
	        success_2 = backtest_df["二星命中"].sum()
	        success_1 = backtest_df["一星命中"].sum()
	        
	        # 計算勝率
	        win_rate_3 = (success_3 / total_tests * 100) if total_tests > 0 else 0
	        win_rate_2 = (success_2 / total_tests * 100) if total_tests > 0 else 0
	        
	        # --- 3. 顯示儀表板 (含基準權重標註) ---
	        st.subheader("🏁 三星命中率回測總結")
	        
	        # 修正後的基準權重顯示 (建議緊跟在標題後)
	        st.caption(f"📊 本次報告基準權重：鄰居 **{sidebar_weights['neighbor']}** | 連動 **{sidebar_weights['trend']}** | 回流 **{sidebar_weights['flow']}** | 遺漏 **{sidebar_weights['omit']}**")
	
	        c1, c2, c3, c4 = st.columns(4)
	        c1.metric("回測總期數", f"{total_tests} 期")
	        c2.metric("三星成功", f"{success_3} 次", f"{win_rate_3:.1f}%")
	        c3.metric("二星命中", f"{success_2} 次", f"{win_rate_2:.1f}%")
	        c4.metric("一星命中", f"{success_1} 次")
	
	        # --- 4. 顯示詳細回測清單 ---
	        st.write("### 📝 詳細模擬紀錄與命中詳情")
	        
	        def highlight_hits(row):
	            val = row['最高單期命中']
	            if val == 3: 
	                return ['background-color: #ff4b4b; color: white; font-weight: bold'] * len(row)
	            elif val == 2: 
	                return ['background-color: #ffaa00; color: black; font-weight: bold'] * len(row)
	            elif val == 1: 
	                return ['background-color: #fff3cd; color: black'] * len(row)
	            return [''] * len(row)
	
	        st.dataframe(
	            backtest_df.style.apply(highlight_hits, axis=1),
	            use_container_width=True,
	            height=500
	        )
	
	        # --- 5. 檔案下載功能 ---
	        st.write("---")
	        import datetime
	        current_time = datetime.datetime.now().strftime("%m%d_%H%M")
	        
	        try:
	            start_id = backtest_df["期數"].iloc[0]
	        except:
	            start_id = "report"
	
	        file_output_name = f"bingo_backtest_{start_id}_{current_time}.csv"
	        csv_data = backtest_df.to_csv(index=False).encode('utf-8-sig')
	        
	        st.download_button(
	            label=f"📥 下載報表 ({current_time})",
	            data=csv_data,
	            file_name=file_output_name,
	            mime="text/csv",
	            help=f"點擊下載回測詳細紀錄。檔名：{file_output_name}",
	            use_container_width=True
	        )
	        
	        # --- 6. 權重優化建議 ---
	        st.divider()
	        with st.expander("💡 如何解讀這份報告並優化權重？"):
	            st.markdown(f"""
	            - **當前三星率：{win_rate_3:.1f}%**
	            - **當前二星率：{win_rate_2:.1f}%**
	            
	            **優化策略：**
	            1. **如果二星很多但三星為 0**：代表號碼抓對了但分散在不同期，建議調高「短期連動」權重。
	            2. **如果連一星都很少**：代表策略偏離，建議執行「智慧校準」。
	            3. **命中號碼檢查**：觀察中獎號碼是否符合預期邏輯。
	            """)
                
# --- 1. 先定義變數 (確保按鈕執行前變數已存在) ---
col_s, col_e = st.columns(2)
with col_s:
    # 這裡定義 start_r，預設值設為 11
    start_r = st.number_input("排名起點", min_value=1, max_value=80, value=11, step=1)
with col_e:
    # 這裡定義 end_r，預設值設為 13
    end_r = st.number_input("排名終點", min_value=1, max_value=80, value=13, step=1)

# 在此之前應先定義好 start_r 與 end_r (例如透過 st.number_input)
if st.button(f"🚀 執行排名 {start_r}-{end_r} 回測"):
    with st.spinner(f"正在模擬「排名 {start_r}-{end_r}」策略回測..."):
        # 執行回測：傳入自定義的 start_r 與 end_r
        backtest_df = run_backtest_rank_11_13(df, sidebar_weights, use_ai_calibration, start_r=start_r, end_r=end_r)
    
    if backtest_df is None or backtest_df.empty:
        st.warning("⚠️ 回測未產生任何結果，請確認數據源是否完整。")
    else:
        # --- 數據處理 ---
        total_tests = len(backtest_df)
        success_3 = backtest_df["三星成功"].sum()
        success_2 = backtest_df["二星命中"].sum()
        success_1 = backtest_df["一星命中"].sum()
        
        win_rate_3 = (success_3 / total_tests * 100) if total_tests > 0 else 0
        win_rate_2 = (success_2 / total_tests * 100) if total_tests > 0 else 0
        
        # --- 顯示儀表板 ---
        st.subheader(f"🏁 排名 {start_r}-{end_r} 策略回測總結")
        st.caption(f"📊 基準權重：鄰居 **{sidebar_weights['neighbor']}** | 連動 **{sidebar_weights['trend']}** | 遺漏 **{sidebar_weights['omit']}**")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("回測總期數", f"{total_tests} 期")
        c2.metric("三星成功", f"{success_3} 次", f"{win_rate_3:.1f}%")
        c3.metric("二星命中", f"{success_2} 次", f"{win_rate_2:.1f}%")
        c4.metric("一星命中", f"{success_1} 次")

        # --- 詳細清單與染色 ---
        st.write(f"### 📝 詳細模擬紀錄 ({start_r}-{end_r} 名策略)")
        
        # 定義專用的染色函式（維持原狀，使用 '最高單期命中'）
        def highlight_rank_hits(row):
            val = row['最高單期命中']
            if val == 3: 
                return ['background-color: #ff4b4b; color: white; font-weight: bold'] * len(row)
            elif val == 2: 
                return ['background-color: #ffaa00; color: black; font-weight: bold'] * len(row)
            elif val == 1: 
                return ['background-color: #fff3cd; color: black'] * len(row)
            return [''] * len(row)

        st.dataframe(
            backtest_df.style.apply(highlight_rank_hits, axis=1),
            use_container_width=True,
            height=500
        )

	


	
st.divider()
st.subheader("🎯 歷史最優組別偵測 (近50期嚴謹回測)")

if st.button("🔥 一鍵分析 3-13 名各組勝率"):
    with st.spinner("正在進行深度數據對齊... 請稍候"):
        # 直接呼叫新方法
        best_groups_df = analyze_group_performance(df, sidebar_weights)
        
        # 顯示結果表格
        st.dataframe(
            best_groups_df.style.highlight_max(axis=0, subset=['綜合評分'], color='#3d1111'),
            use_container_width=True
        )
        
        # 推薦標示
        top_group = best_groups_df.iloc[0]["名次組別"]
        st.info(f"💡 根據這 50 期的嚴謹對齊數據，目前**「{top_group}」**的表現最為突出，建議優先參考。")






















































































