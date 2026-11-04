"""Monochrome Penpot presentation shared by the interactive Streamlit screens."""
from html import escape

import plotly.express as px
import streamlit as st

from src.app._font_css import RUBIK_FONT_CSS

PALETTE = ['#252525', '#929292', '#C5C5C5', '#555555']
PAGE_COPY = {
    'Thanh khoản': ('Thanh khoản', 'Quan sát độ sâu sổ lệnh, chênh lệch giá và phạm vi dữ liệu.'),
    'Mô phỏng thực hiện lệnh': ('Mô phỏng thực hiện lệnh', 'A1 · Ước tính kết quả quét ba mức giá trên phần khối lượng quan sát được.'),
    'Stress danh mục': ('Stress thanh khoản danh mục', 'B5 · So sánh khả năng bao phủ khi độ sâu mua suy giảm theo kịch bản giả định.'),
}


def configure():
    px.defaults.color_discrete_sequence = PALETTE
    st.markdown('<style>' + RUBIK_FONT_CSS + '''
      html,body,[data-testid="stApp"],input,textarea,button,select{font-family:Rubik,Arial,sans-serif!important}
      .stApp{background:#FFF;color:#161616}
      [data-testid="stMainBlockContainer"]{max-width:1560px;padding:2rem 2rem 3rem}
      h1,h2,h3,h4{font-family:Rubik,Arial,sans-serif!important;color:#111;letter-spacing:-.025em}
      h1{font-size:2rem!important;font-weight:600!important;padding-top:.3rem}
      h2{font-size:1.3rem!important}h3{font-size:1.05rem!important;font-weight:600!important}
      [data-testid="stSidebar"]{background:#F7F7F7;border-right:1px solid #DEDEDE;min-width:230px;max-width:270px}
      [data-testid="stSidebar"]>div:first-child{padding-top:1.7rem}
      .ll-brand{display:flex;align-items:center;gap:10px;margin:0 0 28px}
      .ll-mark{width:34px;height:34px;background:#000;border-radius:6px;display:flex;align-items:flex-end;justify-content:center;gap:3px;padding:7px;box-sizing:border-box}
      .ll-mark i{display:block;width:4px;background:white}.ll-mark i:nth-child(1){height:10px}.ll-mark i:nth-child(2){height:16px}.ll-mark i:nth-child(3){height:21px}
      .ll-brand strong{font-size:18px;font-weight:600;letter-spacing:-.5px}.ll-brand small{display:block;color:#686868;font-size:10px;margin-top:2px}
      .ll-eyebrow{font-size:10px;color:#686868;letter-spacing:.08em;margin:14px 0 12px}
      [data-testid="stSidebar"] [data-testid="stRadio"] label{font-size:13px}
      [data-testid="stSidebar"] .st-key-app_page [role="radiogroup"]{gap:8px}
      [data-testid="stSidebar"] .st-key-app_page [role="radiogroup"] label{padding:11px 12px;border-radius:6px;width:100%;margin:0}
      [data-testid="stSidebar"] .st-key-app_page [role="radiogroup"] label:has(input:checked){background:#000;color:#FFF}
      [data-testid="stSidebar"] .st-key-app_page [role="radiogroup"] label:has(input:checked) p{color:#FFF}
      [data-testid="stSidebar"] .st-key-app_page [role="radiogroup"] label>div:first-child{display:none}
      .ll-breadcrumb{font-size:12px;color:#686868;border-bottom:1px solid #DEDEDE;padding:0 0 20px;margin-bottom:18px}
      .ll-description{font-size:14px;color:#686868;margin:0 0 20px;line-height:1.6}
      .ll-source-note{padding:11px 14px;background:#F1F1F1;border-radius:6px;font-size:12px;line-height:1.6;color:#414141;margin:8px 0 18px}
      .ll-chip{display:inline-block;border:1px solid #DEDEDE;border-radius:5px;padding:4px 8px;color:#555;font-size:10px;margin:3px 8px 3px 0}
      .ll-footer{border-top:1px solid #DEDEDE;padding-top:16px;margin-top:24px;color:#686868;font-size:11px}
      [data-testid="stMetric"]{background:#FFF;border:1px solid #DEDEDE;border-radius:8px;padding:16px 18px;height:100%;min-height:116px}
      [data-testid="stMetricLabel"] p{font-size:12px;color:#686868}
      [data-testid="stMetricValue"]{font-size:1.8rem;font-weight:600;color:#161616;letter-spacing:-.03em}
      [data-testid="stVerticalBlockBorderWrapper"]{border-color:#DEDEDE!important;border-radius:8px!important}
      [data-testid="stBaseButton-primary"],[data-testid="stBaseButton-secondary"],.stDownloadButton button{border-radius:24px;min-height:38px;font-size:13px}
      [data-testid="stBaseButton-primary"]{background:#000;border-color:#000;color:#FFF}
      [data-testid="stBaseButton-secondary"],.stDownloadButton button{background:#FFF;border:1px solid #686868;color:#161616}
      [data-baseweb="select"]>div,[data-testid="stNumberInput"]>div{border-color:#999;border-radius:5px;background:#FFF}
      [data-testid="stWidgetLabel"] p{font-size:12px;color:#686868}
      [data-testid="stCaptionContainer"] p{font-size:11px;line-height:1.6;color:#686868}
      [data-testid="stAlert"]{border-radius:6px;font-size:12px}
      [data-testid="stDataFrame"]{border:0;border-radius:5px}
      [data-testid="stExpander"]{border-color:#DEDEDE}
      @media(max-width:800px){
        [data-testid="stMainBlockContainer"]{padding:1.4rem 1rem 2rem}
        h1{font-size:1.6rem!important}
        [data-testid="stMain"] [data-testid="stHorizontalBlock"]{flex-wrap:wrap}
        [data-testid="stMain"] [data-testid="stColumn"]{min-width:min(100%,280px);flex:1 1 280px!important}
      }
    </style>''', unsafe_allow_html=True)


def brand():
    st.sidebar.markdown('''<div class="ll-brand"><span class="ll-mark" aria-hidden="true"><i></i><i></i><i></i></span>
      <div><strong>LiquidityLab</strong><small>NEO · DNSE</small></div></div>
      <div class="ll-eyebrow">PHÂN TÍCH THANH KHOẢN</div>''', unsafe_allow_html=True)


def header(page):
    title, description = PAGE_COPY[page]
    st.markdown(f'<div class="ll-breadcrumb">Phân tích / {escape(title)}</div>', unsafe_allow_html=True)
    st.title(title)
    st.markdown(f'<p class="ll-description">{escape(description)}</p>', unsafe_allow_html=True)


def note(text):
    st.markdown(f'<div class="ll-source-note">{escape(text)}</div>', unsafe_allow_html=True)


def chips(*labels):
    st.markdown(''.join(f'<span class="ll-chip">{escape(label)}</span>' for label in labels), unsafe_allow_html=True)


def chart(fig, height=310):
    fig.update_layout(template='plotly_white', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='#FFFFFF',
        height=height, margin=dict(t=40, b=35, l=25, r=20), font=dict(family='Rubik, Arial, sans-serif', color='#252525', size=11),
        title_font=dict(size=14), legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        colorway=PALETTE)
    fig.update_xaxes(gridcolor='#EFEFEF', zerolinecolor='#DEDEDE')
    fig.update_yaxes(gridcolor='#EFEFEF', zerolinecolor='#DEDEDE')
    st.plotly_chart(fig, width='stretch', theme=None)


def footer():
    st.markdown('<div class="ll-footer">Giá: VND/CP · Khối lượng: CP · Ba mức giá quan sát được · '
                'Kết quả mô phỏng không bảo đảm khớp lệnh.</div>', unsafe_allow_html=True)
