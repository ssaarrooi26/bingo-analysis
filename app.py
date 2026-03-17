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
    計算區間熱力統計
    """
    intervals = ["01-10", "11-20", "21-30", "31-40", "41-50", "51-60", "61-70", "71-80"]
    stats = {intv: 0 for intv in intervals}
    last_draw = df.iloc[0]
    ball_cols = [c for c in df.columns if str(c).isdigit()]
    
    for col in ball_cols:
        val = last_draw[col]
        if pd.notnull(val):
            try:
                num = int(val)
                idx = (num - 1) // 10
                if 0 <= idx < len(intervals):
                    stats[intervals[idx]] += 1
            except:
                continue
    return pd.DataFrame([stats])

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
    
    # --- 關鍵修正：精準取得上期號碼 (必須大於等於 1) ---
    last_draw_row = df.iloc[0]
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

    # 連動響應 (分析最近 50 期)
    limit = min(len(df)-1, 50)
    for i in range(limit):
        hist_row = df.iloc[i+1]
        # 取得該歷史期數開出的號碼
        hist_nums = [n for n in ball_cols if pd.to_numeric(hist_row[n], errors='coerce') >= 1]
        hist_set = set([str(n).zfill(2) for n in hist_nums])
        
        # 若與最新一期有交集 (連動關係)
        if hist_set.intersection(set(last_draw_nums)):
            weight = weights['trend'] if i < 10 else 1.0
            # 該期的「前一期」(即第 i 期) 開出的號碼視為潛力拖牌
            potential_row = df.iloc[i]
            for n in ball_cols:
                if pd.to_numeric(potential_row[n], errors='coerce') >= 1:
                    n_str = str(n).zfill(2)
                    if n_str in scores:
                        scores[n_str] += weight

    # --- 維度二：遺漏節奏 ---
    for num, o in omissions.items():
        n_str = str(num).zfill(2)
        if n_str in scores:
            # 針對熱門遺漏值加分
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
    
    # 排除上期已開出的 20 碼
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

def run_backtest(df, weights):
    import pandas as pd
    
    # --- 參數設定 ---
    test_range = 50   # 執行 50 次模擬
    window = 5        # 每次模擬觀察後續 5 期的表現
    results = []
    
    # 1. 取得資料表中屬於「球號」的欄位清單 (確保留有補零格式如 '01')
    ball_cols = [c for c in df.columns if str(c).isdigit()]
    
    # 2. 開始回溯循環
    for i in range(window, test_range + window):
        # 防呆：如果剩餘數據不足以進行 50 期回測，則提前中斷
        if i + 50 >= len(df): 
            break 
        
        # 模擬「當時」的時間點：
        # current_df 為當時看到的「過去歷史」
        current_df = df.iloc[i:]  
        # actual_future_5 為當時的「未來開獎結果」
        actual_future_5 = df.iloc[i-window:i] 
        
        # 3. 呼叫你的核心演算法：計算遺漏、區間統計並產出 3 個建議號碼
        omissions = calculate_omission(current_df, ball_cols) 
        interval_stats = get_interval_stats(current_df)
        
        # 取得建議號碼 (呼叫你原本的 smart_pick 函式)
        # 注意：這裡解構回傳值，只取前三個建議號碼 [0]
        recs, _ = smart_pick_3(current_df, omissions, interval_stats, None, weights=weights)
        
        # 強制轉為 set 並補零，確保比對時格式一致 (例如 '01' == '01')
        recs_set = set([str(n).zfill(2) for n in recs])
        
        # 4. 核心比對邏輯：找出這 5 期中「單期最高命中數」
        max_hits_in_5_draws = 0
        winning_nums_list = [] # 紀錄中獎的號碼
        
        for _, row in actual_future_5.iterrows():
            # 取得該期真正開出的 20 個號碼 (判定值 >= 1 的欄位名稱)
            current_draw = []
            for c in ball_cols:
                val = pd.to_numeric(row[c], errors='coerce')
                if pd.notnull(val) and val >= 1:
                    current_draw.append(str(c).zfill(2))
            
            # 計算建議號碼與開獎號碼的交集
            hits = recs_set.intersection(set(current_draw))
            current_hit_count = len(hits)
            
            # 更新這 5 期中的最高紀錄
            if current_hit_count > max_hits_in_5_draws:
                max_hits_in_5_draws = current_hit_count
                winning_nums_list = list(hits) # 紀錄最高命中時中獎的號碼
        
        # 5. 取得期號並紀錄結果
        draw_id = df.index[i] # 假設你的 Index 是期號 (如 BINGO 期號)
        
        results.append({
            "期數": draw_id,
            "建議號碼": ", ".join(recs),
            "命中號碼": ", ".join(winning_nums_list) if winning_nums_list else "無",
            "最高單期命中": max_hits_in_5_draws,
            "三星成功": 1 if max_hits_in_5_draws == 3 else 0,
            "二星成功": 1 if max_hits_in_5_draws == 2 else 0,
            "一星成功": 1 if max_hits_in_5_draws == 1 else 0
        })
        
    return pd.DataFrame(results)

def optimize_weights(df, base_weights):
    best_win_rate = -1
    best_weights = base_weights.copy()
    optimization_results = []
    
    # 1. 自動抓取權重字典中所有的 Key (不論中文或英文)
    # 限制測試前 3 個權重，避免組合爆炸 (3^3 = 27 組，3^4 = 81 組)
    all_keys = list(base_weights.keys())
    target_keys = all_keys[:3] 
    
    adj_range = [-0.1, 0, 0.1]
    total_combinations = len(adj_range) ** len(target_keys)
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    count = 0
    
    status_text.write(f"正在交叉測試 {total_combinations} 組權重組合 (優化目標: {', '.join(target_keys)})...")

    # 2. 開始排列組合測試
    for adjs in itertools.product(adj_range, repeat=len(target_keys)):
        test_weights = base_weights.copy()
        
        for i, key in enumerate(target_keys):
            # 自動計算並確保權重不小於 0
            new_val = float(base_weights[key]) + adjs[i]
            test_weights[key] = round(max(0.0, new_val), 2)
        
        # 3. 執行回測與統計
        try:
            res_df = run_backtest(df, test_weights)
            
            # 統計三星勝率與二星近彈數
            win_rate = (res_df["是否成功(三星)"].sum() / len(res_df)) * 100
            near_miss_rate = (res_df["最高單期命中"] == 2).sum()
            
            optimization_results.append({
                "權重變動": str(adjs),
                "三星率": win_rate,
                "二星數": near_miss_rate,
                "詳細配置": test_weights.copy()
            })
            
            # 更新最優解
            if win_rate > best_win_rate:
                best_win_rate = win_rate
                best_weights = test_weights.copy()
            elif win_rate == best_win_rate:
                # 如果三星率一樣，選二星數較高的組合
                current_best_near = (pd.DataFrame(optimization_results)["二星數"].max() 
                                     if optimization_results else 0)
                if near_miss_rate > current_best_near:
                    best_weights = test_weights.copy()

        except Exception as e:
            st.error(f"回測計算發生錯誤: {e}")
            break
            
        count += 1
        progress_bar.progress(count / total_combinations)
        
    status_text.success("尋優計算完成！")
    progress_bar.empty()
    
    return best_weights, optimization_results

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
        backtest_df = run_backtest(df, sidebar_weights)
    
    # --- 1. 錯誤檢查機制 ---
    if backtest_df is None or backtest_df.empty:
        st.warning("⚠️ 回測未產生任何結果，請確認數據源是否完整（建議至少需 100 期歷史數據）。")
    else:
        # --- 2. 符合新統計定義的數據處理 ---
        total_tests = len(backtest_df)
        success_3 = backtest_df["三星成功"].sum()
        success_2 = backtest_df["二星成功"].sum()
        success_1 = backtest_df["一星成功"].sum()
        
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
                

    st.divider()
    st.subheader("🧪 權重 AI 自動尋優")
    st.write("系統將以目前設定為基準，自動測試 27 種微調組合，尋找三星勝率最高的設定。")
    
    if st.button("🔍 啟動自動尋優 (耗時約 30-60 秒)"):
        with st.spinner("AI 正在瘋狂運算中..."):
            best_w, all_res = optimize_weights(df, backtest_weights)
            
            # 轉成 DF 排序顯示
            res_summary = pd.DataFrame(all_res).sort_values(by=["三星率", "二星數"], ascending=False)
            
            st.success(f"✅ 尋優完成！找到最佳三星率：{res_summary.iloc[0]['三星率']:.2f}%")
            
            # 顯示推薦的權重
            st.write("### 🏆 推薦最佳權重配置：")
            cols = st.columns(4)
            cols[0].metric("鄰居權重", f"{best_w['neighbor_weight']:.2f}")
            cols[1].metric("能量回流", f"{best_w['energy_weight']:.2f}")
            cols[2].metric("區間熱力", f"{best_w['interval_weight']:.2f}")
            cols[3].metric("遺漏節奏", f"{best_w['omission_weight']:.2f}")
            
            st.info("💡 你可以直接將這些數值填回左側的權重設定中，再次跑回測驗證。")
            
            with st.expander("查看所有測試組合數據"):
                st.dataframe(res_summary[["權重組合", "三星率", "二星數"]], use_container_width=True)
























































































