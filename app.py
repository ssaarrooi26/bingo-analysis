import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt

# 設定網頁標題與圖標
st.set_page_config(page_title="Bingo 分析大師", layout="wide")

st.title("📊 Bingo Bingo 號碼趨勢隨身版")

# 1. 讀取資料
file_path = '數據分析_2026.csv'

@st.cache_data # 讓資料讀取更快
def load_data():
    try:
        df = pd.read_csv(file_path, encoding='cp950')
    except:
        df = pd.read_csv(file_path, encoding='utf-8')
    return df

df = load_data()

# 2. 側邊欄：設定參數
st.sidebar.header("設定選項")
group_size = st.sidebar.slider("區間期數 (每幾期一組)", 1, 20, 5)
target_numbers = [str(i) for i in range(1, 81)]
existing_cols = [col for col in target_numbers if col in df.columns]

# 3. 功能分頁
tab1, tab2 = st.tabs(["🔥 頻率分佈圖", "心 分段趨勢表"])

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
    
    # 加入色階美化 (熱力圖效果)
    st.dataframe(interval_stats.style.background_gradient(cmap='YlOrRd'))

st.info("💡 提示：手機開啟時，將此網頁「新增至主螢幕」即可像 App 一樣使用。")