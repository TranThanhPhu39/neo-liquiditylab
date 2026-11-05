"""Bounded source-time depth geometry shared by Streamlit and the browser bridge."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import math

from src.models.orderbook import LOCAL_TZ


def numeric(value, *, quantity=False):
    if value is None or isinstance(value,bool):
        return None
    try:
        value=Decimal(str(value))
        if not value.is_finite() or value<0 or (quantity and value!=value.to_integral_value()):
            return None
        if not quantity and value==0:
            return None
        result=int(value) if quantity else float(value)
        return result if math.isfinite(result) and (not quantity or result<=2**53-1) else None
    except (InvalidOperation,ValueError,TypeError,OverflowError):
        return None


def depth_surface(records, symbol, day, at, *, limit=180, gap_seconds=900):
    if at.tzinfo is None or not 2<=limit<=600 or gap_seconds<=0:
        raise ValueError('DEPTH_SURFACE_INVALID_WINDOW')
    rows={}
    for record in records:
        if record.get('symbol')!=symbol:
            continue
        stamp=datetime.fromisoformat(record['source_timestamp'])
        if stamp.tzinfo is None:
            raise ValueError('DEPTH_SURFACE_SOURCE_TIMEZONE_REQUIRED')
        local=stamp.astimezone(LOCAL_TZ)
        if local.date().isoformat()==day and stamp<=at:
            rows[stamp]=(local,record)
    observations=sorted(rows.values(),key=lambda pair:pair[0])
    original=len(observations)
    if original>limit:
        indexes=sorted({round(i*(original-1)/(limit-1)) for i in range(limit)})
        observations=[observations[i] for i in indexes]
    sides={s:dict(x=[],y=[],z=[],cumulative=[],text=[],customdata=[]) for s in ('bid','ask')}
    gaps=0
    previous=None
    tick_values=[]
    for local,record in observations:
        seconds=local.hour*3600+local.minute*60+local.second+local.microsecond/1e6
        if previous is not None and seconds-previous>gap_seconds:
            gaps+=1
            for side in sides.values():
                for field in ('x','z','cumulative'):
                    side[field].append([None]*3)
                side['y'].append([(previous+seconds)/2]*3)
                side['text'].append(['']*3)
                side['customdata'].append([1,2,3])
        for name,side in sides.items():
            prices=[numeric(record.get(f'{name}_price_{i}')) for i in (1,2,3)]
            quantities=[numeric(record.get(f'{name}_vol_{i}'),quantity=True) for i in (1,2,3)]
            values=[q if p is not None else None for p,q in zip(prices,quantities)]
            running=0
            cumulative=[]
            for value in values:
                running=None if value is None or running is None else running+value
                cumulative.append(running)
            side['x'].append(prices);side['y'].append([seconds]*3)
            side['z'].append(values);side['cumulative'].append(cumulative)
            side['text'].append([local.strftime('%H:%M:%S')]*3)
            side['customdata'].append([1,2,3])
        previous=seconds
        tick_values.append(seconds)
    ticks=[tick_values[i] for i in sorted({round(i*(len(tick_values)-1)/4) for i in range(5)})] if tick_values else []
    return dict(symbol=symbol,day=day,points=len(observations),available_points=original,gaps=gaps,
        tickvals=ticks,ticktext=[f'{int(t)//3600:02d}:{int(t)%3600//60:02d}' for t in ticks],sides=sides,
        units_verified=all(r.get('unit_verification_status') in ('EMPIRICALLY_VERIFIED','OFFICIALLY_VERIFIED') for _,r in observations) if observations else False)


COLORS={'bid':[[0,'#e6f4fa'],[.35,'#95c9df'],[.7,'#427f9f'],[1,'#173d56']],
        'ask':[[0,'#e6f8f6'],[.35,'#a0dcd7'],[.7,'#4ba6a1'],[1,'#24666d']]}


def figure_spec(payload, side='bid', cumulative=False):
    if side not in ('bid','ask','both'):
        raise ValueError('DEPTH_SURFACE_INVALID_SIDE')
    traces=[]
    for name in ('bid','ask') if side=='both' else (side,):
        grid=payload['sides'][name]
        z=grid['cumulative'] if cumulative else grid['z']
        label='Bid · Chờ mua' if name=='bid' else 'Ask · Chờ bán'
        hover=label+'<br>Nguồn %{text}<br>Mức %{customdata}<br>Giá %{x:,.0f} VND<br>Khối lượng %{z:,.0f} CP<extra></extra>'
        if payload['points']>1:
            traces.append(dict(type='surface',name=label,x=grid['x'],y=grid['y'],z=z,text=grid['text'],
                customdata=grid['customdata'],connectgaps=False,showscale=False,showlegend=side=='both',
                colorscale=COLORS[name],hovertemplate=hover,opacity=.96,
                lighting=dict(ambient=.75,diffuse=.7,specular=.12,roughness=.8),
                contours=dict(z=dict(show=True,usecolormap=True,highlight=False))))
        stride=max(1,math.ceil(len(z)/24))
        for i in range(0,len(z),stride):
            if not any(v is not None for v in z[i]):
                continue
            traces.append(dict(type='scatter3d',mode='lines+markers',name=label,
                x=grid['x'][i],y=grid['y'][i],z=z[i],text=grid['text'][i],customdata=grid['customdata'][i],
                connectgaps=False,showlegend=False,line=dict(color='#477c99' if name=='bid' else '#438f91',width=1),
                marker=dict(size=2,color='#477c99' if name=='bid' else '#438f91'),hovertemplate=hover,opacity=.7))
    axis=dict(showbackground=True,backgroundcolor='#fbfdfe',gridcolor='#dce7ee',zeroline=False)
    layout=dict(height=520,margin=dict(l=0,r=0,t=10,b=0),paper_bgcolor='white',
        font=dict(family='Rubik, Arial, sans-serif',color='#526477',size=11),
        uirevision=payload['symbol']+'-'+payload['day'],
        scene=dict(xaxis={**axis,'title':dict(text='Giá · VND/CP'),'tickformat':',.0f'},
            yaxis={**axis,'title':dict(text='Thời gian nguồn · UTC+7'),'tickvals':payload['tickvals'],'ticktext':payload['ticktext']},
            zaxis={**axis,'title':dict(text='CP tích lũy' if cumulative else 'Khối lượng · CP'),'rangemode':'tozero'},
            aspectmode='manual',aspectratio=dict(x=1.25,y=2,z=.9),
            camera=dict(eye=dict(x=1.65,y=1.65,z=1.05)),dragmode='orbit'))
    return dict(data=traces,layout=layout)
