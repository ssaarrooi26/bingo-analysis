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
def test_scraping():
    url = "https://lotto.auzo.tw/RK.php"
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.encoding = 'utf-8'
        
        if response.status_code != 200:
            return None, f"連線失敗，代碼：{response.status_code}"
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 改用更寬鬆的方式尋找含有數據的表格
        tables = soup.find_all('table')
        if not tables:
            return None, "找不到任何表格 (Table)"

        # 遍歷表格尋找含有「期數」字眼的列
        target_row = None
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) > 5:  # Bingo Bingo 至少有 20 碼 + 期數，欄位一定很多
                    # 排除掉標題列 (如果第一格內容是"期數"二字就跳過)
                    first_cell = cells[0].get_text(strip=True)
                    if "期" in first_cell or first_cell.isdigit():
                        if first_cell.isdigit(): # 找到真正的數字期數了
                            target_row = cells
                            break
            if target_row: break

        if not target_row:
            return None, "無法定位到有效的開獎資料列"

        # 提取資料
        draw_id = target_row[0].get_text(strip=True)
        
        # 提取所有數字並過濾 1-80
        numbers = []
        for cell in target_row:
            val = cell.get_text(strip=True).lstrip('0')
            if val.isdigit() and 1 <= int(val) <= 80:
                # 補回 0 (例如 5 變 05) 保持格式統一
                numbers.append(val.zfill(2))

        if len(numbers) < 20:
            return None, f"抓取號碼不足 (僅抓到 {len(numbers)} 碼)"

        return draw_id, numbers[:20]

    except Exception as e:
        return None, f"程式發生錯誤: {str(e)}"

# 新增寫入功能函數
def update_multiple_to_gsheets(new_data_list):
    """
    new_data_list 是一個清單，內容為 [(draw_id, [numbers]), (draw_id, [numbers]), ...]
    """
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        
        scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
        client = gspread.authorize(creds)
        sheet = client.open("數據分析_2026").sheet1
        
        # 1. 一次性讀取現有期數，避免在迴圈中重複讀取
        existing_ids = set(sheet.col_values(1))
        
        rows_to_insert = []
        for draw_id, numbers in sorted(new_data_list, key=lambda x: x[0]): # 按期數由舊到新排
            if str(draw_id) in existing_ids:
                continue
            
            # 繼承你優秀的對位邏輯
            row_data = [""] * 81
            row_data[0] = draw_id
            for num_str in numbers:
                num_int = int(num_str)
                if 1 <= num_int <= 80:
                    row_data[num_int] = num_str
            
            rows_to_insert.append(row_data)
        
        if not rows_to_insert:
            return "ℹ️ 無新資料需要寫入。"

        # 2. 關鍵優化：批量插入 (使用 insert_rows，一次通訊解決所有新資料)
        # index=2 代表插入在標題列下方
        sheet.insert_rows(rows_to_insert, row_index=2)
        
        return f"✅ 成功！已批量完成 {len(rows_to_insert)} 筆數據同步。"
        
    except Exception as e:
        return f"❌ 寫入失敗: {str(e)}"

# 設定你的 Google 試算表 CSV 導出連結
SHEET_URL = "https://docs.google.com/spreadsheets/d/1n7JFERmqVCUHwpueBoCH9CKMHqjIaaEKqkDSkjjBmZM/export?format=csv"

# 設定網頁標題與圖標
st.set_page_config(page_title="Bingo 分析大師", layout="wide")

st.title("📊 Bingo Bingo 號碼趨勢隨身版")

# 讀取資料 (加上快取機制)
# ttl=60 代表每 60 秒會自動檢查一次 Google 試算表有沒有新資料
@st.cache_data(ttl=60)
def load_data(url):
    # Google Sheets 導出的 CSV 統一都是 utf-8，不需要擔心編碼問題
    df = pd.read_csv(url)

#  確保「期數」這欄被視為數字（避免 100 排在 2 前面）
    if '期數' in df.columns:
        df['期數'] = pd.to_numeric(df['期數'], errors='coerce')
        
        # 2. 強制降序排列：大期數（最新）排在最上面
        # ascending=False 代表由大到小排
        df = df.sort_values(by='期數', ascending=False).reset_index(drop=True)
    return df

try:
    df = load_data(SHEET_URL)
    st.success("✅ 數據已從雲端同步")
except Exception as e:
    st.error(f"❌ 讀取失敗，請檢查網址或共用設定：{e}")
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
    if target_numbers is None:
        # 自動抓取 01-80 的欄位名稱
        target_numbers = [str(i).zfill(2) for i in range(1, 81) if str(i).zfill(2) in df.columns]
    omission_dict = {}
    
    # 確保 df 是按期數從大到小排 (最新在最上面)
    # 若你的 DataFrame 索引本身就是最新的在上面，此處排序可選擇性保留
    df_sorted = df.sort_values(by='期數', ascending=False).reset_index(drop=True)
    
    for num in target_numbers:
        # 找到該號碼欄位中，第一個「不是空值」的索引位置
        # 因為 df 已經重設索引且降序排，索引值剛好就代表遺漏期數
        not_null_indices = df_sorted[df_sorted[num].notnull()].index
        
        if not not_null_indices.empty:
            # 第一個出現的位置索引即為遺漏期數
            # 例如索引 0 有出，遺漏為 0；索引 5 才有，代表遺漏 5 期
            omission_dict[num] = int(not_null_indices[0])
        else:
            # 如果整張表都沒出現過，設為資料總長度
            omission_dict[num] = len(df_sorted)
            
    return omission_dict
    
def smart_pick_3(df, omissions, interval_stats, latest_draw_id, weights=None, enable_defense=False):
    import random
    import pandas as pd
    import streamlit as st
    
    # --- 權重初始化 ---
    # 進攻與防禦模式共享基礎權重，但防禦模式會額外啟用過熱扣分與能量回流
    if weights is None:
        weights = {'neighbor': 4.5, 'flow': 4.0, 'trend': 3.5, 'omit': 2.5}
    
    # 1. 初始化 Session State (僅在啟用防守模式時重要)
    if 'pick_history' not in st.session_state:
        st.session_state.pick_history = {}
        
    ball_cols = [c for c in df.columns if str(c).isdigit()]
    last_draw_row = df.iloc[0]
    last_draw_nums = [n for n in last_draw_row.index if n in ball_cols and last_draw_row.notnull()[n]]
    
    # 初始化評分表
    scores = {str(i).zfill(2): 0.0 for i in range(1, 81)}
    
    # --- 維度一：鄰居與連動 (先前方案的核心) ---
    # 鄰居加分：回歸「無差別加分」，找回補位感
    for num in last_draw_nums:
        n_int = int(num)
        for diff in [-1, 1]:
            nb = str(n_int + diff).zfill(2)
            if nb in scores:
                # 為了讓防禦模式更有感，若開啟防禦，鄰居分數稍微降低，否則維持高強度
                w_nb = weights['neighbor'] if not enable_defense else weights['neighbor'] * 0.6
                scores[nb] += w_nb

    # 連動響應：維持極短期與長期權重
    for i in range(min(len(df)-1, 50)):
        current_set = set([n for n in df.iloc[i+1].index if n in ball_cols and df.iloc[i+1].notnull()[n]])
        if current_set.intersection(set(last_draw_nums)):
            weight = weights['trend'] if i < 10 else 1.0
            for num in [n for n in df.iloc[i].index if n in ball_cols]:
                if num in scores: scores[num] += weight

    # --- 維度二：遺漏節奏 ---
    for num, o in omissions.items():
        if num in scores:
            if o in [3, 5, 8]: scores[num] += weights['omit']
            # 強制冷卻力道：防禦模式下扣分更重，避免開關無感
            omit_penalty = -6.0 if enable_defense else -2.0
            if o == 0: scores[num] += omit_penalty

    # --- 維度三：區間熱力 (防守模式開關) ---
    zone_cols = [c for c in interval_stats.columns if '-' in str(c)]
    if zone_cols:
        if not enable_defense:
            # 【進攻模式】：追熱邏輯 (先前方案)
            top_zone_name = interval_stats[zone_cols].iloc[-1].idxmax()
            try:
                start, end = map(int, str(top_zone_name).split('-'))
                for i in range(start, end + 1):
                    n_str = str(i).zfill(2)
                    if n_str in scores: scores[n_str] += 1.5 
            except: pass
        else:
            # 【防守模式】：過熱保護與能量回流 (目前方案)
            # 強化干預力道，確保號碼會跳轉
            for z in zone_cols:
                start, end = map(int, z.split('-'))
                count = sum(1 for n in last_draw_nums if start <= int(n) <= end)
                if count >= 4:
                    for i in range(start, end + 1): 
                        scores[str(i).zfill(2)] -= 10.0 # 扣分從 3 改為 10
                    adj_low, adj_high = str(start-1).zfill(2), str(end+1).zfill(2)
                    if adj_low in scores: scores[adj_low] += weights['flow']
                    if adj_high in scores: scores[adj_high] += weights['flow']

    # --- 維度四：權重衰減 (僅在防守模式啟用) ---
    if enable_defense:
        for num in scores:
            decay_count = st.session_state.pick_history.get(num, 0)
            if decay_count >= 3: scores[num] -= (decay_count * 2.0)

    # 3. 排序與輸出
    scored_candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    final_candidates = [n[0] for n in scored_candidates if n[0] not in last_draw_nums]
    top_3 = final_candidates[:3]
    
    # 僅在啟用防守時更新歷史紀錄
    if enable_defense:
        for num in top_3: st.session_state.pick_history[num] = st.session_state.pick_history.get(num, 0) + 1
        for num in list(st.session_state.pick_history.keys()):
            if num not in top_3: del st.session_state.pick_history[num]
            
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
    # 回測參數
    test_range = 50 
    window = 5       
    results = []
    
    # 1. 建立標準球號清單
    ball_cols = [str(i).zfill(2) for i in range(1, 81) if str(i).zfill(2) in df.columns]
    
    for i in range(window, test_range + window):
        if i + 50 >= len(df): 
            break 
        
        # 模擬當時的歷史 (i期之後)
        current_df = df.iloc[i:]  
        # 之後的實際結果 (i-5 到 i-1 期)
        actual_future_5 = df.iloc[i-window:i] 
        
        # 統計與選號
        omissions = calculate_omission(current_df, ball_cols) 
        interval_stats = get_interval_stats(current_df)
        recs = smart_pick_3_backtest(current_df, omissions, interval_stats, weights)
        recs_set = set(recs) # 轉 set 加速比對
        
        # --- 核心邏輯修正：單期比對 ---
        max_hits_in_5_draws = 0
        hit_details = "" # 紀錄哪一期中了幾顆
        
        for _, row in actual_future_5.iterrows():
            # 取得該單期的開獎號碼 (20顆)
            current_draw = [str(int(row[c])).zfill(2) for c in ball_cols if pd.notnull(row[c])]
            # 計算該單期中了幾顆
            current_hits = len(recs_set.intersection(set(current_draw)))
            
            # 紀錄這 5 期中表現最好的一期
            if current_hits > max_hits_in_5_draws:
                max_hits_in_5_draws = current_hits
        
        # 判定成功：必須有一期中滿 3 顆
        is_success = 1 if max_hits_in_5_draws == 3 else 0
        
        # 安全取得期號
        draw_id = df.index[i] 
        
        results.append({
            "期數": draw_id,
            "建議號碼": ", ".join(recs),
            "最高單期命中": max_hits_in_5_draws,
            "是否成功(三星)": is_success
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

# 1. 加入模式開關 (這會決定 smart_pick_3 跑進攻還是規避邏輯)
is_defensive = st.sidebar.toggle("🛡️ 啟用風險規避模式", value=False)

# 2. 定義滑桿
sw_n = st.sidebar.slider("鄰居觸發", 1.0, 10.0, 4.5, key="real_n")
sw_t = st.sidebar.slider("短期連動", 1.0, 10.0, 3.5, key="real_t")
sw_f = st.sidebar.slider("能量回流", 0.0, 5.0, 2.0, key="real_f")
sw_o = st.sidebar.slider("遺漏節奏", 1.0, 5.0, 2.5, key="real_o")

# 3. 組合成函數看得懂的字典 (這裡的 key 必須跟 smart_pick_3 內部一致)
sidebar_weights = {
    'neighbor': sw_n, 
    'trend': sw_t, 
    'flow': sw_f, 
    'omit': sw_o
}

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
        
        # 2. 顯示最新期數資訊
        st.info(f"📅 當前最新期數：**{latest_draw_id}**")
        
        # 3. 計算五期循環邏輯
        # 餘數為 0 代表剛好整除
        remainder = latest_draw_id % 5
        
        if remainder == 0:
            st.success("🎯 當前已達成 5 期循環！數據已完整，適合進行下波預測分析。")
        else:
            wait_count = 5 - remainder
            st.warning(f"⏳ 目前處於循環中：第 **{remainder}** 期")
            st.write(f"👉 距離下一個完整區間（5期）還需等待：**{wait_count}** 期")
            
        st.divider() # 分隔線
    
    # 1. 準備基礎數據：計算每個號碼的總出現次數與最後出現期數
    latest_counts = df[existing_cols].notnull().sum()
    
    # 2. 演算法 A：熱門號碼 (近期最常出現的前 10 名)
    hot_numbers = latest_counts.nlargest(10).index.tolist()
    
    # 3. 演算法 B：潛力冷號 (目前沒開，但總頻率不低的號碼)
    # 這裡我們隨機從出現次數較少的後 20 名中選 5 個，避免每次都一樣
    cold_numbers = latest_counts.nsmallest(20).index.tolist()
    suggested_cold = random.sample(cold_numbers, 5)

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
    
    # 視覺化呈現：最冷門的號碼
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.write("🔥 **目前最久未開號碼**")
        st.table(omission_df.head(10).reset_index(drop=True))
        
    with col2:
        st.write("📈 **遺漏分佈圖**")
        # 使用直條圖顯示遺漏狀況
        st.bar_chart(omission_df.set_index('球號')['遺漏期數'])

    # --- 結合你的 5 期循環邏輯 ---
    max_omission_num = omission_df.iloc[0]['球號']
    max_omission_val = omission_df.iloc[0]['遺漏期數']
    
    st.info(f"💡 **觀察建議**：號碼 **{max_omission_num}** 已經連續 **{max_omission_val}** 期未開出了。搭配當前循環剩餘期數，可以觀察其是否會在近期反彈。")

    # 取得最新兩期數據
    if len(df) >= 2:
        last_row = df.iloc[0]  # 最新一期
        prev_row = df.iloc[1]  # 前一期
        
        # 找出有開出的號碼 (欄位值不為空)
        current_nums = set([col for col in existing_cols if pd.notnull(last_row[col])])
        prev_nums = set([col for col in existing_cols if pd.notnull(prev_row[col])])
        
        # 1. 連莊號碼分析
        repeat_nums = current_nums.intersection(prev_nums)
        
        st.subheader("1. 🔄 連莊追蹤 (Repeated)")
        col_r1, col_r2 = st.columns([1, 2])
        col_r1.metric("本期連莊數", f"{len(repeat_nums)} 碼")
        col_r2.write(f"最新連莊號碼： {', '.join(sorted(list(repeat_nums))) if repeat_nums else '無'}")
        
        st.divider()

        # 2. 黃金區間分析 (每 10 號一區)
        st.subheader("2. 🏆 黃金區間 (Sections)")
        section_data = {}
        for i in range(0, 80, 10):
            start, end = i + 1, i + 10
            label = f"{start}-{end}"
            # 統計這 10 個號碼在最新一期開出幾顆
            section_cols = [str(n) for n in range(start, end + 1) if str(n) in existing_cols]
            count = sum(pd.notnull(last_row[section_cols]))
            section_data[label] = count
            
        st.bar_chart(pd.Series(section_data), color="#f4a261")
        max_sec = max(section_data, key=section_data.get)
        st.caption(f"💡 目前最旺區間：{max_sec} 區 (開出 {section_data[max_sec]} 碼)")

        st.divider()

        # 3. 尾數熱度分析 (0-9 尾)
        st.subheader("3. 🔢 尾數分析 (Last Digit)")
        tail_data = {str(i): 0 for i in range(10)}
        for num in current_nums:
            tail = str(int(num) % 10)
            tail_data[tail] += 1
            
        # 轉換成 DataFrame 顯示更精美
        tail_df = pd.DataFrame(list(tail_data.items()), columns=['尾數', '開出個數'])
        st.dataframe(
            tail_df.set_index('尾數').T.style.background_gradient(cmap="Greens", axis=1)
        )

        


        # UI 顯示
        # 呼叫更新後的函數
        recommendations, all_scores = smart_pick_3(df, omissions, interval_stats, latest_draw_id, weights=sidebar_weights, enable_defense=is_defensive)
        # --- 新增：當前選號模式說明 ---
        if not is_defensive:
            st.subheader("🔥 當前模式：進攻型 ")
            st.caption("🚀 策略重點：**鄰居強力補位**、**熱門區域追蹤**。適合連號頻出的強勢盤勢。")
            st.markdown("---")
        else:
            st.subheader("🛡️ 當前模式：風險規避型)")
            st.caption("⚖️ 策略重點：**避開飽和區域**、**號碼疲勞降溫**。適合號碼分佈散亂的盤勢。")
            st.markdown("---")

        st.subheader("🎯 高精度交叉驗證選碼")
        cols = st.columns(3)
        for i, num in enumerate(recommendations):
            cols[i].metric(label=f"建議號碼 {i+1}", value=num, delta=f"權重分: {all_scores[num]}")
        
        # --- 新增：顯示前 10 名的高分潛力股 ---
        st.write("---")
        st.subheader("📊 號碼潛力價值排行榜 (Top 10)")
        
        # 將分數轉為 DataFrame 方便顯示
        score_df = pd.DataFrame(list(all_scores.items()), columns=['號碼', '加權總分'])
        score_df = score_df.sort_values(by='加權總分', ascending=False).head(10).reset_index(drop=True)
        
        # 使用 Streamlit 的 dataframe 顯示
        st.dataframe(score_df.style.highlight_max(axis=0, color='#ff4b4b'), use_container_width=True)
        
        st.caption("註：加權總分綜合了「歷史拖牌連動」、「遺漏轉折週期」與「當前熱門區間」三大指標。")

        # --- 手動重置衰減狀態按鈕 ---
        st.write("---")
        st.subheader("⚙️ 系統控制")
        
        if st.button("🔴 清空推薦歷史 (重置衰減狀態)"):
            # 清空 session_state 中的紀錄
            st.session_state.pick_history = {}
            st.success("已成功重置！所有號碼的「疲勞期」紀錄已清空，下一期將重新計算。")
            # 強制重新執行，讓畫面立即更新
            st.rerun()
        
        # 顯示目前的追蹤狀態（可選，方便你觀察誰正在被衰減）
        if st.session_state.pick_history:
            with st.expander("查看目前追蹤中的號碼"):
                st.write("以下號碼若連續出現，將會逐期扣分：")
                for num, count in st.session_state.pick_history.items():
                    st.text(f"號碼 {num}：已連續推薦 {count} 期")

        # --- 關鍵修正區塊 ---
        # 這裡用 st.write 先測試，確保邏輯有跑進來
        # remainder == 0 是指 5 的倍數 (例如期數 115)
        # remainder == 4 是指 5 的倍數減 1 (例如期數 114)
        if remainder == 0 or remainder == 4:
            st.caption(f"🛡️ 目前期數 {latest_draw_id} (餘數 {remainder})：已啟動「循環末端避熱」機制。")
        else:
            st.caption(f"ℹ️ 目前期數 {latest_draw_id} (餘數 {remainder})：循環進行中，維持常規分析。")

        # 綜合預測邏輯
        st.divider()
        st.subheader("🎲 綜合推薦組合")
        # 這裡結合最旺區間 + 熱門尾數
        top_tail = max(tail_data, key=tail_data.get)
        st.success(f"建議關注：{max_sec} 區間的號碼，並優先考慮「{top_tail}」尾的組合。")

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
            # 執行回測
            backtest_df = run_backtest(df, backtest_weights)
    
            # --- 1. Bug 檢查機制 (保留並修正欄位名稱) ---
            if backtest_df is None or backtest_df.empty:
                st.warning("回測未產生任何結果，請檢查數據源是否足夠（需大於 100 期）。")
            # 檢查新版欄位「是否成功(三星)」是否存在
            elif "是否成功(三星)" not in backtest_df.columns:
                st.error("回測資料表格式錯誤，請檢查欄位定義。")
                st.write("目前的欄位有：", backtest_df.columns.tolist()) 
            else:
                # --- 2. 符合新統計定義的數據處理 ---
                total_tests = len(backtest_df)
                success_tests = backtest_df["是否成功(三星)"].sum()
                # 統計「差一點就中」的期數 (單期中 2 顆)
                near_misses = (backtest_df["最高單期命中"] == 2).sum()
                
                # 避免除以 0 的安全檢查
                win_rate = (success_tests / total_tests * 100) if total_tests > 0 else 0
                
                # --- 3. 顯示儀表板 ---
                st.subheader("🏁 單期三星回測報告")
                c1, c2, c3 = st.columns(3)
                c1.metric("回測總期數", f"{total_tests} 期")
                c2.metric("三星成功次數", f"{success_tests} 次")
                c3.metric("三星總勝率", f"{win_rate:.2f}%")
                
                # 輔助提示
                if near_misses > 0:
                    st.write(f"💡 備註：在 50 期中，有 **{near_misses}** 期達到了「單期中 2 顆」，距離三星僅一步之遙！")
                
                # --- 4. 顯示詳細回測清單 (優化顏色邏輯) ---
                st.write("### 📝 詳細回測紀錄")
                
                def highlight_stars(val):
                    if val == 3: 
                        return 'background-color: #ff4b4b; color: white; font-weight: bold' # 三星達成：鮮紅色
                    if val == 2: 
                        return 'background-color: #ffaa00; color: black; font-weight: bold' # 二星：橘色
                    if val == 1: 
                        return 'background-color: #fff3cd; color: black' # 一星：淡黃色
                    return ''
    
                # 使用新欄位「最高單期命中」進行染色
                st.dataframe(
                    backtest_df.style.applymap(highlight_stars, subset=['最高單期命中']),
                    use_container_width=True,
                    height=400
                )
                
                # --- 5. 權重優化建議 (根據三星邏輯調整語義) ---
                st.divider()
                st.info("💡 **權重優化建議**：\n"
                        "* **若勝率為 0% 但二星(橘色)很多**：代表號碼抓取正確但集中度不足，建議微調「短期連動」或「區間熱力」。\n"
                        "* **若連一星(黃色)都很少**：代表策略完全偏離，建議大幅調高「鄰居觸發」或「遺漏節奏」權重。\n"
                        "* **能量回流建議**：若命中號碼總是在開出後才出現在預測中，請提高「能量回流」權重。")

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














































































