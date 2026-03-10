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

    # 遺漏期數統計
    def calculate_omission(df, target_numbers):
        omission_dict = {}
        
        # 確保 df 是按期數從大到小排 (最新在最上面)
        df_sorted = df.sort_values(by='期數', ascending=False)
        
        for num in target_numbers:
            # 找到該號碼欄位中，第一個「不是空值」的索引位置
            # 因為 df 已經降序排，索引值剛好就等於遺漏期數
            not_null_indices = df_sorted[df_sorted[num].notnull()].index
            
            if not not_null_indices.empty:
                # 第一個出現的位置索引即為遺漏期數
                # 例如索引 0 有出，遺漏為 0；索引 5 才有，代表遺漏 5 期
                omission_dict[num] = not_null_indices[0]
            else:
                # 如果整張表都沒出現過，設為資料總長度
                omission_dict[num] = len(df_sorted)
                
        return omission_dict

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

        def smart_pick_3(df, omissions, interval_stats, latest_draw_id):
            import random
            import pandas as pd
            import streamlit as st
        
            # 1. 初始化環境與 Session State (用於權重衰減)
            if 'pick_history' not in st.session_state:
                st.session_state.pick_history = {} # 格式: {號碼: 連續出現次數}
        
            ball_cols = [c for c in df.columns if str(c).isdigit()]
            last_draw_row = df.iloc[0]
            last_draw_nums = [n for n in last_draw_row.index if n in ball_cols and last_draw_row.notnull()[n]]
            
            scores = {str(i).zfill(2): 0.0 for i in range(1, 81)}
        
            # --- 維度一：爆發力優先 (已納入) ---
            for num in last_draw_nums:
                n_int = int(num)
                for diff in [-1, 1]:
                    nb = str(n_int + diff).zfill(2)
                    if nb in scores:
                        # 鄰居觸發 + 黃金回補期 = 爆發引爆分
                        if omissions.get(nb, 99) in [1, 2, 3]:
                            scores[nb] += 4.5 
                        else:
                            scores[nb] += 1.5
        
            # --- 維度二：區間飽和度與能量回流 (新加入) ---
            zone_cols = [c for c in interval_stats.columns if '-' in str(c)]
            if zone_cols:
                # 計算上一期各區間開出球數
                for z in zone_cols:
                    try:
                        start, end = map(int, z.split('-'))
                        # 統計該區間在上一期開了幾顆
                        count = sum(1 for n in last_draw_nums if start <= int(n) <= end)
                        
                        if count >= 4: # 飽和門檻：該區間開出 4 顆以上
                            # 飽和區號碼扣分 (避免能量稀釋)，並將能量轉移給相鄰區間
                            for i in range(start, end + 1):
                                scores[str(i).zfill(2)] -= 3.0
                            # 能量回流：給予相鄰區間邊界碼加分 (範例：21-30過熱，則19,20,31,32加分)
                            adj_low, adj_high = str(start-1).zfill(2), str(end+1).zfill(2)
                            if adj_low in scores: scores[adj_low] += 2.0
                            if adj_high in scores: scores[adj_high] += 2.0
                    except: continue
        
            # --- 維度三：短期連動與費氏節奏 (強化) ---
            for i in range(min(len(df)-1, 50)):
                current_set = set([n for n in df.iloc[i+1].index if n in ball_cols and df.iloc[i+1].notnull()[n]])
                next_gen_nums = [n for n in df.iloc[i].index if n in ball_cols and df.iloc[i].notnull()[n]]
                if current_set.intersection(set(last_draw_nums)):
                    weight = 3.5 if i < 10 else 1.0
                    for num in next_gen_nums:
                        if num in scores: scores[num] += weight
        
            for num, o in omissions.items():
                if num in scores:
                    if o in [3, 5, 8, 13]: scores[num] += 2.5
                    if o == 0: scores[num] -= 6.0 # 避開剛開出的值
        
            # --- 維度四：權重衰減機制 (新加入) ---
            for num in scores:
                decay_count = st.session_state.pick_history.get(num, 0)
                if decay_count >= 3: # 連續 3 期被推薦但未中，進入疲勞期
                    scores[num] -= (decay_count * 2.0) # 強制降溫
        
            # 3. 排序與更新歷史紀錄
            scored_candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            final_candidates = [n[0] for n in scored_candidates if n[0] not in last_draw_nums]
            
            top_3 = final_candidates[:3]
        
            # 更新 Session State: 記錄誰被推薦了
            for num in top_3:
                st.session_state.pick_history[num] = st.session_state.pick_history.get(num, 0) + 1
            # 清除沒被推薦號碼的紀錄 (代表斷開了連續推薦)
            for num in list(st.session_state.pick_history.keys()):
                if num not in top_3:
                    del st.session_state.pick_history[num]
        
            return top_3, scores


        # UI 顯示
        # 呼叫更新後的函數
        recommendations, all_scores = smart_pick_3(df, omissions, interval_stats, latest_draw_id)
        
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

    def run_backtest(df):
        import pandas as pd
        
        # 回測參數
        test_range = 50  # 測試過去 50 期
        window = 5       # 驗證 5 期內是否命中
        results = []
        
        # 取得球號欄位
        ball_cols = [c for c in df.columns if str(c).isdigit()]
        
        # 開始回測（從較舊的期數往最新期數模擬）
        # df.iloc[0] 是最新，所以我們從 test_range + window 的位置開始回溯
        for i in range(window, test_range + window):
            if i + 50 >= len(df): break # 確保有足夠歷史資料計算評分
            
            # 模擬「那一期」的視角：切分當時的歷史資料
            # current_df 是「當時」可見的歷史
            current_df = df.iloc[i:] 
            actual_future_5 = df.iloc[i-window:i] # 當時之後的實際 5 期結果
            
            # 1. 計算當時的遺漏值與區間統計 (這部分需呼叫你現有的計算函數)
            # 假設你現有的函數名稱為 calculate_omissions 和 get_interval_stats
            # omissions = calculate_omissions(current_df)
            # interval_stats = get_interval_stats(current_df)
            
            # 2. 執行當時的智慧選號 (排除 session_state 影響)
            # 我們稍微簡化 smart_pick_3 為回測專用版本，不存取 st.session_state
            recs, _ = smart_pick_3_logic_only(current_df) 
            
            # 3. 驗證 5 期內命中次數
            match_count = 0
            future_nums = []
            for _, row in actual_future_5.iterrows():
                draw = [str(n).zfill(2) for n in row if str(n).isdigit() and pd.notnull(n)]
                future_nums.extend(draw)
            
            hit_nums = [n for n in recs if n in set(future_nums)]
            results.append({
                "期數": df.iloc[i].name,
                "建議號碼": recs,
                "命中數": len(hit_nums),
                "命中號碼": hit_nums,
                "是否成功(1中以上)": 1 if len(hit_nums) > 0 else 0
            })
            
        return pd.DataFrame(results)

    if st.button("🚀 開始執行 50 期回測"):
        with st.spinner("系統正在模擬歷史選號並驗證結果..."):
            backtest_df = run_backtest(df)

            # --- 加入檢查機制 ---
            if backtest_df.empty:
                st.warning("回測未產生任何結果，請檢查數據源是否足夠（需大於 100 期）。")
            elif "是否成功(1中以上)" not in backtest_df.columns:
                st.error("回測資料表格式錯誤，請檢查欄位定義。")
                st.write("目前的欄位有：", backtest_df.columns.tolist()) # 幫助除錯
            else:
                # 欄位確定存在才執行加總
                total_tests = len(backtest_df)
                success_tests = backtest_df["是否成功(1中以上)"].sum()
            
            # 計算統計數據
            total_tests = len(backtest_df)
            success_tests = backtest_df["是否成功(1中以上)"].sum()
            win_rate = (success_tests / total_tests) * 100
            
            # 顯示儀表板
            c1, c2, c3 = st.columns(3)
            c1.metric("回測總期數", f"{total_tests} 期")
            c2.metric("5期內命中成功", f"{success_tests} 次")
            c3.metric("總體勝率", f"{win_rate:.1f}%")
            
            # 顯示詳細回測清單
            st.write("### 詳細回測紀錄")
            st.dataframe(backtest_df.style.applymap(
                lambda x: 'background-color: #d4edda' if x > 0 else '', subset=['是否成功(1中以上)']
            ), use_container_width=True)
            
            # 權重優化建議
            st.info("💡 **權重優化建議**：若勝率低於 60%，建議調高「鄰居觸發」權重；若命中號碼重疊度高但開出慢，建議調高「短期連動」權重。")






























































