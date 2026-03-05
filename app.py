import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

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
tab1, tab2 = st.tabs(["🔥 頻率分佈圖", "分段趨勢表"])

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
    
    # 2. 設定色階 (這裡使用 'YlOrRd' 黃到紅)
    # axis=None 代表對整個表格進行全域比較，而不僅是單行或單列比較
    # 這樣「全表」出現 3 次的格子顏色都會一模一樣
     
    styled_df = interval_stats.style.background_gradient(
        cmap='RdYlGn', 
        axis=None,    # 關鍵：全域比較，相同數值必同色
        low=0,        # 設定顏色範圍的最小值
        high=0.5      # 稍微調高上限，可以讓顏色對比更明顯（可視情況調整）
    ).format("{:.0f}") # 確保顯示的是整數
    
    # 3. 顯示表格
    st.dataframe(styled_df, height=600)


st.info("💡 提示：手機開啟時，將此網頁「新增至主螢幕」即可像 App 一樣使用。")









