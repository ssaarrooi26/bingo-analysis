import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import random
import requests
from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials

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
def update_to_gsheets(draw_id, numbers):
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        
        scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
        # 從 Streamlit Secrets 讀取 (請確保雲端後台已設定)
        creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
        client = gspread.authorize(creds)
        
        # 請確保這裡的名稱與你的 Google Sheet 檔案名稱完全一致，永遠抓最左邊的第一個分頁
        sheet = client.open("數據分析_2026").sheet1
        
        # 檢查期數是否已存在
        existing_ids = sheet.col_values(1)
        if str(draw_id) in existing_ids:
            return f"ℹ️ 期數 {draw_id} 已存在，無需重複寫入。"
        
        # 建立對位資料行
        # 建立一個包含 81 個欄位的列表，初始值全部為空字串 ""
        # index 0 是期數，index 1~80 對應號碼 1~80
        row_data = [""] * 81
        row_data[0] = draw_id  # 第一欄放入期數
        
        for num_str in numbers:
            num_int = int(num_str) # 轉成整數，例如 "05" -> 5
            if 1 <= num_int <= 80:
                # 關鍵：號碼是幾號，就填在第幾欄 (例如 5 號填在 index 5)
                # 這樣在 Google Sheets 裡，5 號就會剛好在 E 欄 (第 5 欄) 下方
                row_data[num_int] = num_str 
        
        # 3. 插入到試算表第二列
        sheet.insert_row(row_data, index=2)
        
        return f"✅ 成功！期數 {draw_id} 已完成號碼對位寫入。"
        
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
    
    # 1. 建立標準球號清單 (01-80) 以供後續比對與統計使用
    # 確保這些欄位確實存在於 df 中
    ball_cols = [str(i).zfill(2) for i in range(1, 81) if str(i).zfill(2) in df.columns]
    
    for i in range(window, test_range + window):
        if i + 50 >= len(df): 
            break 
        
        # 模擬當時的歷史：從第 i 期往後的資料
        current_df = df.iloc[i:]  
        # 之後的實際結果：第 i 期之前的 5 期 (i-5 到 i-1)
        actual_future_5 = df.iloc[i-window:i] 
        
        # --- 關鍵修正：傳入正確的參數數量 ---
        # 確保呼叫的名稱 (calculate_omissions) 與你定義的名稱一致
        omissions = calculate_omissions(current_df, ball_cols) 
        
        # 確保 get_interval_stats 也能正常運作
        interval_stats = get_interval_stats(current_df)
        
        # 執行選號
        recs = smart_pick_3_backtest(current_df, omissions, interval_stats, weights)
        
        # 驗證命中
        future_nums = set()
        for _, row in actual_future_5.iterrows():
            # 取得該期所有開出的號碼並轉為 zfill(2) 格式
            draw = [str(int(row[c])).zfill(2) for c in ball_cols if pd.notnull(row[c])]
            future_nums.update(draw)
        
        hit_nums = [n for n in recs if n in future_nums]
        
        # 安全取得期號
        draw_id = df.index[i] 
        
        results.append({
            "期數": draw_id,
            "建議號碼": ", ".join(recs),
            "命中數": len(hit_nums),
            "命中號碼": ", ".join(hit_nums),
            "是否成功(1中以上)": 1 if len(hit_nums) > 0 else 0
        })
        
    return pd.DataFrame(results)

# 2. 側邊欄：設定參數
st.sidebar.header("🚀 數據同步工具")
if st.sidebar.button("🔄 抓取並同步至雲端"):
    with st.sidebar:
        with st.spinner("正在執行自動化流程..."):
            # A. 先抓取
            draw_id, result = test_scraping()
            
            if draw_id and len(result) == 20:
                st.info(f"🔍 偵測到官網期數：{draw_id}")
                
                # B. 執行寫入
                write_msg = update_to_gsheets(draw_id, result)
                st.write(write_msg)
                
                # C. 如果寫入成功，強制清除快取讓畫面更新
                if "成功" in write_msg:
                    st.cache_data.clear()
                    st.success("數據已刷新，請查看下方報表")
                    # st.rerun() # 如果想讓畫面立即跳動可加上這行
            else:
                st.error(f"抓取異常：{result}")

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
            # 【關鍵修正】：傳入 backtest_weights 參數
            backtest_df = run_backtest(df, backtest_weights)

            # --- 檢查機制 ---
            if backtest_df is None or backtest_df.empty:
                st.warning("回測未產生任何結果，請檢查數據源是否足夠（需大於 100 期）。")
            elif "是否成功(1中以上)" not in backtest_df.columns:
                st.error("回測資料表格式錯誤，請檢查欄位定義。")
                st.write("目前的欄位有：", backtest_df.columns.tolist()) 
            else:
                # 計算統計數據
                total_tests = len(backtest_df)
                success_tests = backtest_df["是否成功(1中以上)"].sum()
                # 避免除以 0 的安全檢查
                win_rate = (success_tests / total_tests * 100) if total_tests > 0 else 0
                
                # 顯示儀表板
                st.subheader("🏁 回測總結報告")
                c1, c2, c3 = st.columns(3)
                c1.metric("回測總期數", f"{total_tests} 期")
                c2.metric("成功獲中次數", f"{success_tests} 次")
                c3.metric("預測勝率", f"{win_rate:.1f}%")
                
                # 顯示詳細回測清單
                st.write("### 📝 詳細回測紀錄")
                
                # 優化：根據命中數給予不同顏色
                def highlight_hits(val):
                    if val >= 2: return 'background-color: #ffcccc; color: black; font-weight: bold' # 中兩顆以上給紅色
                    if val == 1: return 'background-color: #d4edda; color: black' # 中一顆給綠色
                    return ''

                st.dataframe(
                    backtest_df.style.applymap(highlight_hits, subset=['命中數']),
                    use_container_width=True,
                    height=400
                )
                
                # 權重優化建議
                st.divider()
                st.info("💡 **權重優化建議**：\n"
                        "* 若勝率低於 60%，建議調高「鄰居觸發」權重。\n"
                        "* 若命中號碼經常在開出後才出現，建議調高「能量回流」權重。")








































































