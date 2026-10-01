import sys, pickle, json, numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0,'/workspace/medwork')
from fonts import pool
S=120
_fc={}
def font(p,s=S):
    k=(p,s)
    if k not in _fc: _fc[k]=ImageFont.truetype(p,s)
    return _fc[k]
def render(p,text,track=0.0,s=S):
    f=font(p,s); W=int(sum(f.getlength(c) for c in text)+abs(track)*len(text)+s*2); H=int(s*2.2)
    im=Image.new('L',(W,H),0); d=ImageDraw.Draw(im); x=s*0.5; B=int(s*1.5)
    for c in text:
        d.text((x,B),c,font=f,fill=255,anchor='ls'); x+=f.getlength(c)+track
    a=np.array(im); ys,xs=np.where(a>40)
    if len(xs)==0: return None
    x0,x1,y0,y1=xs.min(),xs.max()+1,ys.min(),ys.max()+1
    return a[y0:y1,x0:x1], (x0-s*0.5, y0-B, x1-s*0.5, y1-B)   # bbox rel. to origin at baseline
def deskew(L):
    q=np.array(L['quad']); v=q[1]-q[0]; ang=float(np.degrees(np.arctan2(v[1],v[0])))
    m=L['mask'].astype(np.uint8)*255
    if abs(ang)<1.5: return m, 0.0
    pad=max(m.shape); c=cv2.copyMakeBorder(m,pad,pad,pad,pad,cv2.BORDER_CONSTANT,0)
    M=cv2.getRotationMatrix2D((c.shape[1]/2,c.shape[0]/2),ang,1.0)
    r=cv2.warpAffine(c,M,(c.shape[1],c.shape[0]),flags=cv2.INTER_LINEAR)
    ys,xs=np.where(r>100); return r[ys.min():ys.max()+1,xs.min():xs.max()+1], ang
def soft(a,b):
    a=cv2.GaussianBlur(a.astype(np.float32)/255,(0,0),1.0); b=cv2.GaussianBlur(b.astype(np.float32)/255,(0,0),1.0)
    return float(np.minimum(a,b).sum()/max(1e-6,np.maximum(a,b).sum()))
def score_line(L,P,kinds=None):
    om,ang=deskew(L); oh,ow=om.shape
    up=max(1,int(np.ceil(40/oh))); tgt=cv2.resize(om,(ow*up,oh*up),interpolation=cv2.INTER_LINEAR)
    text=L['text']; n=len(text); res=[]
    for lbl,p,kind in P:
        if kinds and kind not in kinds: continue
        r=render(p,text)
        if r is None: continue
        a,bb=r; k=oh/a.shape[0]; w0=a.shape[1]*k
        best=None
        # hscale mode
        sx=ow/w0
        cand=cv2.resize(a,(tgt.shape[1],tgt.shape[0]),interpolation=cv2.INTER_AREA)
        sc=soft(tgt,cand)-0.6*abs(np.log(sx))
        best=(sc,'hscale',float(sx),0.0)
        if kind!='script' and n>1:
            t=(ow-w0)/(n-1)/k   # tracking in render px at size S
            if t>-0.12*S:
                r2=render(p,text,track=t)
                if r2 is not None:
                    a2,_=r2; cand=cv2.resize(a2,(tgt.shape[1],tgt.shape[0]),interpolation=cv2.INTER_AREA)
                    sc2=soft(tgt,cand)-(0.15 if t<-0.05*S else 0)
                    if sc2>best[0]: best=(sc2,'track',1.0,float(t/S))
        res.append({'font':lbl,'kind':kind,'score':round(best[0],4),'mode':best[1],'sx':best[2],'track_em':best[3]})
    res.sort(key=lambda r:-r['score'])
    return res, ang
if __name__=='__main__':
    P=pool()
    for side in sys.argv[1:]:
        meta=pickle.load(open(f'meta_{side}.pkl','rb')); out=[]
        for L in meta:
            res,ang=score_line(L,P)
            out.append({'id':L['id'],'text':L['text'],'angle':ang,'top':res[:12]})
            print(L['id'],repr(L['text'][:30]),'->',res[0]['font'],res[0]['score'],res[0]['mode'],'|',res[1]['font'],res[1]['score'],flush=True)
        json.dump(out,open(f'match_{side}.json','w'),indent=1)
