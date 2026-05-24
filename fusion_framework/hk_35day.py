"""
港股融合框架 - 35日调仓（无止损）
"""
import pandas as pd, numpy as np, os, warnings
warnings.filterwarnings('ignore')

REBAL_FREQ = 35
MAX_POSITIONS = 30
MIN_SCORE = 3
INIT_CASH = 1000000
HOLD_MAX = 30

LOG = open(r'E:/quant/fusion_framework/low_log_35day.txt','w',encoding='utf-8')
def W(msg): print(msg); LOG.write(msg+'\n'); LOG.flush()

df = pd.read_csv(r'E:\quant\fusion_framework\df_wrsi_cache.csv')
df['trade_date'] = pd.to_datetime(df['trade_date'])
df['weekly_rsi'] = pd.to_numeric(df['weekly_rsi'], errors='coerce').fillna(50.0)
df['future_ret_5d'] = df.groupby('symbol')['close'].pct_change(5)

from scipy.stats import spearmanr
recent = df[df['trade_date']>'2025-01-01'].dropna(subset=['weekly_rsi','future_ret_5d'])
recent2 = recent.dropna(subset=['resonance']).copy()
recent2['score'] = 0.202*(recent2['weekly_rsi']-50) + (-0.152)*recent2['resonance']
ic_w = spearmanr(recent['weekly_rsi'], recent['future_ret_5d'])[0]
ic_s = spearmanr(recent2['score'], recent2['future_ret_5d'])[0]
W(f'IC wrsi={ic_w:.4f} score={ic_s:.4f}')

ALL_DATES = sorted(df['trade_date'].unique())
recent_dates = ALL_DATES[-500:]

W('预计算信号...')
date_to_buys = {}
date_to_sells = {}
for i, date in enumerate(recent_dates):
    today = df[df['trade_date']==date].copy()
    today = today.dropna(subset=['weekly_rsi','resonance','rsi_14','macd_hist','ma20_ratio','ma60_ratio'])
    if len(today) < 5: continue
    wrsi=today['weekly_rsi'].values.copy(); res=today['resonance'].values.copy()
    rsi_d=today['rsi_14'].values.copy(); macd_h=today['macd_hist'].values.copy()
    ma20r=today['ma20_ratio'].values.copy(); ma60r=today['ma60_ratio'].values.copy()
    for arr,fill in [(wrsi,50.0),(rsi_d,50.0),(macd_h,0.0),(ma20r,1.0),(ma60r,1.0),(res,0.0)]:
        arr[np.isnan(arr)]=fill
    score=0.202*(wrsi-50)+(-0.152)*res
    trend_up=(ma20r>1.0)&(ma60r>1.0); above20=ma20r>1.0; above60=ma60r>1.0; macd_pos=macd_h>0
    ta=np.where(above20&above60&macd_pos&(rsi_d<70),'BUY',
           np.where((rsi_d>=78)|(~above20&(rsi_d>65)),'SELL','HOLD'))
    fm=np.where(score>=MIN_SCORE,'BUY',np.where(score<=-MIN_SCORE,'SELL','HOLD'))
    buy_mat_t={('BUY','BUY'):'STRONG_BUY',('BUY','HOLD'):'BUY',('BUY','SELL'):'BUY',
               ('HOLD','BUY'):'BUY',('HOLD','HOLD'):'HOLD',('HOLD','SELL'):'HOLD',
               ('SELL','BUY'):'BUY',('SELL','HOLD'):'HOLD',('SELL','SELL'):'SELL'}
    sell_mat_t={('BUY','BUY'):'STRONG_BUY',('BUY','HOLD'):'BUY',('BUY','SELL'):'REDUCED',
                ('HOLD','BUY'):'HOLD',('HOLD','HOLD'):'HOLD',('HOLD','SELL'):'SELL',
                ('SELL','BUY'):'REDUCED',('SELL','HOLD'):'SELL',('SELL','SELL'):'SELL'}
    level=np.full(len(today),'HOLD')
    for j in range(len(today)):
        mat=buy_mat_t if bool(trend_up[j]) else sell_mat_t
        level[j]=mat.get((str(fm[j]),str(ta[j])),'HOLD')
    sym_arr=today['symbol'].values; close_arr=today['close'].values
    buys=[(sym_arr[k],score[k],close_arr[k]) for k in range(len(today)) if level[k] in ['BUY','STRONG_BUY']]
    sells=[sym_arr[k] for k in range(len(today)) if level[k] in ['SELL','STRONG_SELL']]
    date_to_buys[date]=sorted(buys,key=lambda x:-x[1])[:MAX_POSITIONS]
    date_to_sells[date]=sells
    if (i+1)%100==0: W(f'  预处理 {i+1}/500')

W(f'调仓周期={REBAL_FREQ}日  持仓上限={MAX_POSITIONS}只  无止损')

def run_bt(cost, label):
    cash=float(INIT_CASH); positions={}; daily_nav=[]; n_buys=n_sells=0
    for i, date in enumerate(recent_dates):
        today=df[df['trade_date']==date]
        for sym in list(positions.keys()):
            pr=today[today['symbol']==sym]
            positions[sym]['cur_price']=float(pr.iloc[0]['close']) if len(pr)>0 else float(
                df[(df['symbol']==sym)&(df['trade_date']<=date)].tail(1).iloc[0]['close'])
            positions[sym]['hold_days']=positions[sym].get('hold_days',0)+1
            if positions[sym]['hold_days']>=HOLD_MAX:
                cash+=positions[sym]['shares']*positions[sym]['cur_price']*(1-cost)
                n_sells+=1; del positions[sym]
        if i%REBAL_FREQ==0:
            for sym in date_to_sells.get(date,[]):
                if sym in positions:
                    pr=today[today['symbol']==sym]
                    px=float(pr.iloc[0]['close']) if len(pr)>0 else positions[sym]['cur_price']
                    cash+=positions[sym]['shares']*px*(1-cost); n_sells+=1; del positions[sym]
            buys=date_to_buys.get(date,[])
            cur_nav=cash+sum(p['shares']*p['cur_price'] for p in positions.values())
            target=cur_nav/len(buys) if buys else 0
            for sym,sc,px in buys:
                if sym in positions or target<=0: continue
                n=int(target/px)
                if n>0 and n*px*(1+cost)<=cash:
                    cash-=n*px*(1+cost); positions[sym]={'shares':n,'cur_price':px,'hold_days':0}; n_buys+=1
        nav=cash+sum(p['shares']*p['cur_price'] for p in positions.values())
        daily_nav.append({'date':date,'nav':nav,'n_pos':len(positions)})
        if (i+1)%100==0: W(f'  {i+1}/500 NAV={nav/1e4:.1f}万 持仓={len(positions)}只')
    nav_df=pd.DataFrame(daily_nav)
    nav_df['ret']=nav_df['nav'].pct_change().fillna(0)
    total=(nav_df.iloc[-1]['nav']/INIT_CASH-1)*100
    ann=((1+total/100)**(250/500)-1)*100
    sharpe=nav_df['ret'].mean()/nav_df['ret'].std()*np.sqrt(250) if nav_df['ret'].std()>1e-10 else 0
    peak=INIT_CASH; max_dd=0.0; dd=0.0
    for v in nav_df['nav']:
        if v>peak: peak=v
        dd=(v-peak)/peak
        if dd<max_dd: max_dd=dd
    W(f'  [{label}] 换手={n_buys+n_sells} 毛={total:+.1f}% 年化={ann:+.1f}% Sharpe={sharpe:.2f} 回撤={max_dd*100:.2f}%')
    return nav_df

W(f'\n情景      成本    换手  毛收益   年化    Sharpe  回撤')
results={}
for cost,label in [(0.003,'乐观'),(0.005,'基准'),(0.007,'现实')]:
    nav_df=run_bt(cost,label); results[label]=nav_df
    if cost==0.007: nav_df.to_csv(r'E:\quant\fusion_framework\low_35day_nav.csv',index=False)

W('基准: 等权top20 +65.0%  年化+29.6%')
W('月频(21日,0.7%): +38.7%  年化+17.8%')
r35=(results['现实'].iloc[-1]['nav']/INIT_CASH-1)*100
W('35日调仓(0.7%): '+str(round(r35,1))+'%')
LOG.close()
print('Done!')
