import pickle, json, math, sys, numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0,'/workspace/medwork')
from groups import group
D='/workspace/medfonts/static/'
FAM={'caps':[('Cinzel',w,D+f'Cinzel-w{w}.ttf',0) for w in (400,500,600,700)],
     'body':[('Crimson Text',400,D+'CrimsonText-Regular.ttf',0),('Crimson Text',600,D+'CrimsonText-SemiBold.ttf',0)],
     'italic':[('EB Garamond Italic',w,D+f'EBGaramond-Italic-w{w}.ttf',0) for w in (400,500,600,700)],
     'script':[('Parisienne',400,D+'Parisienne-Regular.ttf',sw) for sw in range(0,13)]}  # sw in 1/4 src px
SRC={'flyer_front':'/workspace/medella_out/src_flyer_front.png','flyer_back':'/workspace/medella_out/src_flyer_back.png','card_front':'/workspace/medella_out/src_card_front.png'}
Z=4
_fc={}
def F(p,sz):
    k=(p,round(sz,2))
    if k not in _fc: _fc[k]=ImageFont.truetype(p,sz)
    return _fc[k]
def render(path,size,text,cs=0.0,hs=1.0,sw=0):
    """render at px size; glyph-by-glyph (no kerning, like reportlab). returns coverage array (float) and origin (ox,oy) in array coords"""
    f=F(path,size)
    adv=[f.getlength(c) for c in text]
    width=sum(adv)+cs*max(len(text)-1,0)+size*2
    Hh=int(size*2.6)+2*sw; Ww=int(width+size)+2*sw
    im=Image.new('L',(Ww,Hh),0); d=ImageDraw.Draw(im)
    ox=size*0.8+sw; oy=size*1.8+sw; x=ox
    for c,a in zip(text,adv):
        d.text((x,oy),c,font=f,fill=255,anchor='ls',stroke_width=sw,stroke_fill=255)
        x+=a+cs
    a=np.asarray(im,np.float32)/255
    if hs!=1.0:
        a=cv2.resize(a,(max(1,int(round(Ww*hs))),Hh),interpolation=cv2.INTER_AREA if hs<1 else cv2.INTER_LINEAR); ox*=hs
    return a,(ox,oy)
def bbox(a,th=0.35):
    ys,xs=np.where(a>th)
    return xs.min(),ys.min(),xs.max()+1,ys.max()+1
def fit_one(text,grp,cand,W,Hh,maskarea):
    name,w,path,sw=cand
    n=len(text)
    S0=160.0
    a,(ox,oy)=render(path,S0,text,0,1,sw*S0/(Z*20) if False else 0)
    x0,y0,x1,y1=bbox(a); k=Hh/(y1-y0)
    size=S0*k  # src px
    # render at Z*size for real with stroke
    R=size*Z
    a,(ox,oy)=render(path,R,text,0,1,sw)
    x0,y0,x1,y1=bbox(a); h=(y1-y0)/Z; 
    size*=Hh/h; R=size*Z
    a,(ox,oy)=render(path,R,text,0,1,sw); x0,y0,x1,y1=bbox(a)
    w0=(x1-x0)/Z
    extra=W-w0; cs=0.0; hs=1.0
    if grp=='caps':
        cs=extra/max(n-1,1); cs=float(np.clip(cs,-0.04*size,0.7*size))
    elif grp in ('body','italic'):
        cs=extra/max(n-1,1); cs=float(np.clip(cs,-0.03*size,0.05*size))
        w1=w0+cs*(n-1); hs=float(np.clip(W/w1,0.92,1.08))
    else:
        hs=float(np.clip(W/w0,0.80,1.15))
    a,(ox,oy)=render(path,R,text,cs*Z,hs,sw); x0,y0,x1,y1=bbox(a)
    wf=(x1-x0)/Z
    if wf>W*1.01:  # shrink to never exceed original width
        f=W/wf; size*=f; cs*=f
        R=size*Z; a,(ox,oy)=render(path,R,text,cs*Z,hs,sw*1); x0,y0,x1,y1=bbox(a); wf=(x1-x0)/Z
    # area at src res
    small=cv2.resize(a[y0:y1,x0:x1],(max(1,int(round((x1-x0)/Z))),max(1,int(round((y1-y0)/Z)))),interpolation=cv2.INTER_AREA)
    area=(small>0.5).sum()
    return dict(font=name,weight=w,path=path,sw=sw/Z,size=size,cs=cs,hs=hs,wf=wf,hf=(y1-y0)/Z,
                bx0=(x0-ox)/Z,by0=(y0-oy)/Z,area=int(area),score=abs(math.log((area+1)/(maskarea+1))),arr=(a,ox,oy,x0,y0,x1,y1))
def deskew(mask,ang):
    p=int(max(mask.shape)*0.3)+4
    m=np.pad(mask.astype(np.uint8)*255,p)
    c=(m.shape[1]/2,m.shape[0]/2)
    M=cv2.getRotationMatrix2D(c,ang,1.0)
    r=cv2.warpAffine(m,M,(m.shape[1],m.shape[0]),flags=cv2.INTER_LINEAR)
    return r>127,M,p
def color_of(img,e):
    x0,y0,x1,y1=e['ink']; m=e['mask']

    px=img[y0:y1,x0:x1][m].astype(np.float32)
    bg=np.array(e['bg'],np.float32)
    d=np.linalg.norm(px-bg,axis=1)
    sel=px[d>=np.percentile(d,50)]
    return np.median(sel,0)
def cls(c):
    r,g,b=c
    if min(c)>170: return 'light'
    if b-r>18 and b>=g: return 'navy'
    if g-r>5 and g>=b-4: return 'green'
    return 'neutral'
def snap2(cols,gap=18):
    base=[cls(c) for c in cols]; k=list(base)
    for h in set(base):
        if h=='light': continue
        idx=[i for i in range(len(cols)) if base[i]==h]
        L=np.array([np.mean(cols[i]) for i in idx])
        if len(idx)<4: continue
        c1,c2=L.min(),L.max()
        for _ in range(20):
            a=np.abs(L-c1)<=np.abs(L-c2); c1,c2=L[a].mean(),(L[~a].mean() if (~a).any() else c2)
        if c2-c1>gap:
            for i,aa in zip(idx,a): k[i]=h+('_dark' if aa else '_mid')
    out=[]
    for c,kk in zip(cols,k):
        grp=[cols[j] for j in range(len(cols)) if k[j]==kk]
        out.append(np.mean(grp,0) if kk!='light' else np.array([252.,252.,250.]))
    return out,k
def snap(cols,th=38):
    cl=[]
    for i,c in enumerate(cols):
        for g in cl:
            if np.linalg.norm(np.mean([cols[j] for j in g],0)-c)<th: g.append(i); break
        else: cl.append([i])
    out=[None]*len(cols)
    for g in cl:
        m=np.mean([cols[j] for j in g],0)
        for j in g: out[j]=m
    return out
def _mid(q):
    q=np.array(q,float); a=(q[0]+q[3])/2; b=(q[1]+q[2])/2; h=(np.linalg.norm(q[3]-q[0])+np.linalg.norm(q[2]-q[1]))/2
    return a,b,h
def _nd(pt,q):
    a,b,h=_mid(q); d=b-a; n=np.array([-d[1],d[0]])/np.linalg.norm(d)
    return abs(np.dot(pt-a,n))/h
def disentangle(meta):
    """reassign mask components between lines whose ink boxes overlap"""
    for i,a in enumerate(meta):
        others=[]
        for b in meta:
            if b is a: continue
            A=a['ink'];B=b['ink']
            if min(A[2],B[2])-max(A[0],B[0])>0 and min(A[3],B[3])-max(A[1],B[1])>0: others.append(b)
        if not others: continue
        m=a['mask'].astype(np.uint8); n,lab,st,cen=cv2.connectedComponentsWithStats(m,8)
        keep=np.zeros_like(m,bool)
        for k in range(1,n):
            c=cen[k]+np.array(a['ink'][:2],float)
            mine=_nd(c,a['quad'])
            if all(mine<=_nd(c,b['quad']) for b in others): keep|=(lab==k)
        if keep.sum()==0: continue
        ys,xs=np.where(keep); x0,y0,x1,y1=xs.min(),ys.min(),xs.max()+1,ys.max()+1
        a['mask']=keep[y0:y1,x0:x1]; I=a['ink']; a['ink']=[I[0]+x0,I[1]+y0,I[0]+x1,I[1]+y1]
    return meta
BODY_W={'flyer_back':400}
LIGHT_CAPS_W={'flyer_back':500}
INK_CLIP={'flyer_front:93':631}
def run(side):
    meta=pickle.load(open(f'/workspace/medwork/meta_{side}.pkl','rb'))
    meta=disentangle(meta)
    for e in meta:
        if e['id'] in INK_CLIP:
            x0,y0,x1,y1=e['ink']; cx=INK_CLIP[e['id']]
            if x1>cx:
                m=e['mask'][:,:cx-x0]; ys,xs=np.where(m)
                e['mask']=m[ys.min():ys.max()+1,:xs.max()+1]; e['ink']=[x0,y0+ys.min(),x0+xs.max()+1,y0+ys.max()+1]
    img=np.asarray(Image.open(SRC[side]).convert('RGB'))
    items=[]
    for e in meta:
        t=e['text']; g=group(t); q=np.array(e['quad'])
        dx,dy=q[1]-q[0]; ang=math.degrees(math.atan2(dy,dx))
        if abs(ang)<1.2: ang=0.0
        m=e['mask']; M=None; p=0
        if ang:
            r,M,p=deskew(m,ang); ys,xs=np.where(r); bx=(xs.min(),ys.min(),xs.max()+1,ys.max()+1)
            mm=r[bx[1]:bx[3],bx[0]:bx[2]]
        else:
            mm=m; bx=(0,0,m.shape[1],m.shape[0])
        W=bx[2]-bx[0]; Hh=bx[3]-bx[1]; area=int(mm.sum())
        fam=FAM[g]
        if g=='caps': fam=[c for c in fam if c[1]>=500]
        cands=[fit_one(t,g,c,W,Hh,area) for c in fam]
        items.append(dict(e=e,g=g,ang=ang,bx=bx,W=W,H=Hh,area=area,cands=cands,M=M,p=p))
    # global weight for body and italic
    gw={}
    for g in ('body','italic'):
        its=[it for it in items if it['g']==g and len(it['e']['text'])>2]
        if not its: continue
        ws=[c['weight'] for c in its[0]['cands']]
        tot={w:sum(next(c['score'] for c in it['cands'] if c['weight']==w) for it in its) for w in ws}
        gw[g]=min(tot,key=tot.get)
    if g_override:=BODY_W.get(side): gw['body']=g_override
    # majority caps weight per colour class
    from collections import Counter
    _c=[color_of(img,it['e']) for it in items]; _s,_k=snap2(_c)
    for it,kk_ in zip(items,_k): it['ccls']=kk_
    for cc in set(it['ccls'] for it in items):
        its=[it for it in items if it['g']=='caps' and it['ccls']==cc]
        if len(its)>=3:
            W_=Counter(min(it['cands'],key=lambda c:c['score'])['weight'] for it in its).most_common(1)[0][0]
            for it in its: it['capsW']=W_
    res=[]; cols=[]
    for it in items:
        e=it['e']; g=it['g']
        light=cls(color_of(img,e))=='light'
        if g in gw: best=next(c for c in it['cands'] if c['weight']==gw[g])
        else: best=min(it['cands'],key=lambda c:c['score'])
        if g=='caps' and it.get('capsW') and best['score']>next(c['score'] for c in it['cands'] if c['weight']==it['capsW'])-9:
            best=next(c for c in it['cands'] if c['weight']==it['capsW'])
        if g=='caps' and light and best['weight']>500:
            best=next(c for c in it['cands'] if c['weight']==best['weight']-100)
        if g=='caps' and light and side in LIGHT_CAPS_W:
            best=next(c for c in it['cands'] if c['weight']==LIGHT_CAPS_W[side])
        if g=='script' and (light or (best['sw']<0.5 and best['size']<45)) and best['sw']>0:
            best=next(c for c in it['cands'] if c['sw']==0)
        if g=='script' and best['sw']>0:
            cap=round(best['size']*0.004*4)/4
            if best['sw']>cap: best=min(it['cands'],key=lambda c:abs(c['sw']-cap))
        W=it['W']; bx=it['bx']
        tl=bx[0]; tt=bx[1]
        if best['wf']<W*0.985: tl+=(W-best['wf'])/2
        o=(tl-best['bx0'], tt-best['by0'])
        if it['ang']:
            Mi=cv2.invertAffineTransform(it['M'])
            ox,oy=Mi@np.array([o[0],o[1],1.0]); ox-=it['p']; oy-=it['p']
        else: ox,oy=o
        ox+=e['ink'][0]; oy+=e['ink'][1]
        cols.append(color_of(img,e))
        b={k:v for k,v in best.items() if k!='arr'}
        res.append(dict(id=e['id'],text=e['text'],group=g,angle=it['ang'],origin=[float(ox),float(oy)],ink=list(map(int,e['ink'])),tw=int(W),th=int(it['H']),marea=it['area'],**{k:(float(v) if isinstance(v,(np.floating,float)) else v) for k,v in b.items()}))
    sc,kk=snap2(cols)
    PAL={'flyer_front':{'green':(15,51,35),'neutral':(33,32,32),'light':(252,252,250)},
         'flyer_back':{'green':(5,38,32),'navy_dark':(25,43,55),'navy_mid':(55,70,82),'light':(252,252,250)},
         'card_front':{'green':(13,40,17),'neutral':(18,18,19)}}[side]
    sc=[np.array(PAL.get(k_,c),np.float32) for c,k_ in zip(sc,kk)]
    for r_,c,c0,k_ in zip(res,sc,cols,kk): r_['color']=[float(x) for x in c]; r_['color_raw']=[float(x) for x in c0]; r_['cclass']=k_
    json.dump(res,open(f'/workspace/medwork/fit_{side}.json','w'),indent=1)
    print(side,'global weights',gw)
    return res
if __name__=='__main__':
    for s in sys.argv[1:]:
        R=run(s)
        for r in R: print(r['id'],r['font'],r['weight'],'sw%.2f'%r['sw'],'sz%.1f cs%.2f hs%.3f'%(r['size'],r['cs'],r['hs']),'wf/tw %.2f'%(r['wf']/r['tw']),'ang%.1f'%r['angle'],r['cclass'],[int(x) for x in r['color']],r['text'][:30])
