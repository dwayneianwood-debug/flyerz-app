import json,numpy as np
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
_reg={}
def sw(path,text,size):
    if path not in _reg:
        n='H'+str(len(_reg)); pdfmetrics.registerFont(TTFont(n,path)); _reg[path]=n
    return pdfmetrics.stringWidth(text,_reg[path],size)
def blocks(L):
    body=[r for r in L if r['group']=='body' and not r['cclass'].startswith('light') and abs(r['angle'])<0.5]
    body.sort(key=lambda r:(r['origin'][1]))
    B=[]
    for r in body:
        for b in B:
            last=b[-1]
            if abs(r['origin'][0]-b[0]['origin'][0])<9 and 0<r['origin'][1]-last['origin'][1]<2.0*max(r['size'],last['size']) and r['path']==last['path']:
                b.append(r); break
        else: B.append([r])
    return [b for b in B if len(b)>=2]
def harmonize(L,verbose=False):
    out={}
    for b in blocks(L):
        S=float(np.median([r['size'] for r in b]))
        for r in b:
            n=len(r['text'])
            w_old=sw(r['path'],r['text'],r['size'])*r['hs']+r['cs']*(n-1)
            cs_new=(w_old-sw(r['path'],r['text'],S)*r['hs'])/max(1,n-1)
            hs=r['hs']
            if abs(cs_new)>1.0:   # keep tracking sane; absorb rest with horizontal scale (<=3%)
                c=float(np.clip(cs_new,-1.0,1.0)); hs2=(w_old-c*(n-1))/sw(r['path'],r['text'],S)
                if abs(hs2-1)<=0.03: cs_new,hs=c,hs2
                else: continue
            out[r['id']]=dict(size=S,cs=cs_new,hs=hs)
            if verbose: print(f"{r['id']:>16} {r['size']:5.1f}->{S:5.1f} cs {r['cs']:+.2f}->{cs_new:+.2f} hs {hs:.3f} {r['text']}")
        if verbose: print('--')
    return out
if __name__=='__main__':
    for side in ['flyer_front','flyer_back','card_front']:
        print('=====',side); harmonize(json.load(open(f'fit_{side}.json')),True)
