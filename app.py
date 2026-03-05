import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import random

# 1. 設定你的 Google 試算表 CSV 導出連結
SHEET_URL = "https://docs.google.com/spreadsheets/d/1n7JFERmqVCUHwpueBoCH9CKMHqjIaaEKqkDSkjjBmZM/export?format=csv"

# 設定網頁標題與圖標
st.set_page_config(page_title="Bingo 分析大師", layout="wide")

st.title("📊 Bingo Bingo 號碼趨勢隨身版")

# 1. 讀取資料 (加上快取機制)
# ttl=60 代表每 60 秒會自動檢查一次 Google 試算表有沒有新資料
@st.cache_data(ttl=60)
def load_data(url):
    # Google Sheets 導出的 CSV 統一都是 utf-8，不需要擔心編碼問題
    df = pd.read_csv(url)
    return df

try:
    df = load_data(SHEET_URL)
    st.success("✅ 數據已從雲端同步")
except Exception as e:
    st.error(f"❌ 讀取失敗，請檢查網址或共用設定：{e}")
    st.stop()

# 2. 側邊欄：設定參數
st.sidebar.header("設定選項")
group_size = st.sidebar.slider("區間期數 (每幾期一組)", 1, 20, 5)
target_numbers = [str(i) for i in range(1, 81)]
existing_cols = [col for col in target_numbers if col in df.columns]

# 3. 功能分頁
tab1, tab2, tab3 = st.tabs(["🔥 頻率分佈圖", "分段趨勢表", "🔮 智能建議"])

with tab1:
    st.header("1-80 號碼總出現頻率")
    frequency = df[existing_cols].notnull().sum()
    
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(frequency.index, frequency.values, color='skyblue')
    plt.xticks(rotation=90, fontsize=6)
    st.pyplot(fig)

with tab2:
    st.header(f"每 {group_size} 期趨勢分析")
    df['Group'] = (df.index // group_size) + 1
    interval_stats = df.groupby('Group')[existing_cols].apply(lambda x: x.notnull().sum())
    interval_stats.index = [f"第 {i*group_size-(group_size-1)}~{i*group_size} 筆" for i in interval_stats.index]
    
    
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

    # 4. 終極電腦選號 (混合邏輯)
    st.subheader("🎲 綜合推薦組合 (5 碼)")
    # 從熱門選 3 碼 + 冷門選 2 碼
    mix_picks = random.sample(hot_numbers, 3) + random.sample(cold_numbers, 2)
    st.success(f"本日推薦組合： {', '.join(mix_picks)}")

    st.caption("註：預測邏輯基於歷史統計數據，僅供參考。請理性娛樂。")

st.info("💡 提示：手機開啟時，將此網頁「新增至主螢幕」即可像 App 一樣使用。")



















