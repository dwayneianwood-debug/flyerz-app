import sys,json,io,numpy as np,cv2
sys.path.insert(0,'/workspace/medwork')
import pdf as P
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
import pymupdf
DPI=300; K=DPI/25.4
rec=[]
_dt=P.draw_text; _ht=P.heart
def rdt(c,ph,x,y,text,*a,**kw): rec.append(('text',cur,ph,(x,y,text)+a,kw)); return _dt(c,ph,x,y,text,*a,**kw)
def rht(c,ph,*a,**kw): rec.append(('heart',cur,ph,a,kw)); return _ht(c,ph,*a,**kw)
P.draw_text=rdt; P.heart=rht
dummy=canvas.Canvas(io.BytesIO())
for cur in ['flyer_front','flyer_back','card_front']:
    pw,ph=P.SIDE[cur]['page']; P.set_boxes(dummy,pw,ph); P.fitted_side(dummy,cur); dummy.showPage()
cur='card_back'; P.card_back_page(dummy); dummy.showPage()
P.draw_text=_dt; P.heart=_ht
PAGE={'flyer_front':(158,220),'flyer_back':(158,220),'card_front':(100,60),'card_back':(100,60)}
BG={'flyer_front':'bgF_flyer_front.png','flyer_back':'bgF_flyer_back.png','card_front':'bgF_card_front.png','card_back':'bg_card_back_final.png'}
BGDPI={'flyer_front':400,'flyer_back':400,'card_front':600,'card_back':600}
report={}
for side in PAGE:
    items=[r for r in rec if r[1]==side]
    buf=io.BytesIO(); c=canvas.Canvas(buf); pw,ph=PAGE[side]
    for kind,_,ph_,a,kw in items:
        c.setPageSize((pw*mm,ph*mm))
        if kind=='text':
            x,y,text,fp,size=a[:5]; rest=list(a[5:])
            # force black for visibility
            args=[x,y,text,fp,size]+rest
            if len(args)>=8: args[7]=(0,0,0)
            kw=dict(kw); kw['rgb']=(0,0,0) if 'rgb' in kw else kw.get('rgb',(0,0,0))
            if len(args)>=9: args[8]='neutral'
            _dt(c,ph_,*args,**{k:v for k,v in kw.items() if k not in ('rgb','cls')} ,**({} if len(args)>=8 else {'rgb':(0,0,0)}))
        else:
            kw=dict(kw); kw['rgb']=(0,0,0); _ht(c,ph_,*a,**kw)
        c.showPage()
    c.save()
    doc=pymupdf.open(stream=buf.getvalue(),filetype='pdf')
    masks=[]; boxes=[]
    for i,pg in enumerate(doc):
        pix=pg.get_pixmap(dpi=DPI,colorspace=pymupdf.csGRAY)
        a=np.frombuffer(pix.samples,np.uint8).reshape(pix.h,pix.w)
        m=a<200; ys,xs=np.where(m)
        if len(xs)==0: masks.append(None); boxes.append(None); continue
        b=(xs.min(),ys.min(),xs.max()+1,ys.max()+1); boxes.append(b)
        masks.append((b,m[b[1]:b[3],b[0]:b[2]]))
    labels=[(it[3][2] if it[0]=='text' else 'heart') for it in items]
    # pairwise ink overlap (1px dilation = 0.085mm clearance)
    over=[]
    ker=np.ones((3,3),np.uint8)
    for i in range(len(items)):
        if masks[i] is None: continue
        bi,mi=masks[i]
        for j in range(i+1,len(items)):
            if masks[j] is None: continue
            bj,mj=masks[j]
            x0=max(bi[0],bj[0])-2; y0=max(bi[1],bj[1])-2; x1=min(bi[2],bj[2])+2; y1=min(bi[3],bj[3])+2
            if x1<=x0 or y1<=y0: continue
            W_,H_=x1-x0,y1-y0
            A=np.zeros((H_,W_),np.uint8); B=np.zeros((H_,W_),np.uint8)
            def paste(dst,b,m):
                sx0=max(x0,b[0]); sy0=max(y0,b[1]); sx1=min(x1,b[2]); sy1=min(y1,b[3])
                if sx1>sx0 and sy1>sy0: dst[sy0-y0:sy1-y0,sx0-x0:sx1-x0]=m[sy0-b[1]:sy1-b[1],sx0-b[0]:sx1-b[0]]
            paste(A,bi,mi.astype(np.uint8)); paste(B,bj,mj.astype(np.uint8))
            n=int((cv2.dilate(A,ker)&B).sum())
            if n>0: over.append((labels[i],labels[j],n))
    # text vs raster ornaments/icons: busy pixels in background (strong local structure)
    bg=cv2.imread(BG[side]); s=DPI/BGDPI[side]
    bg=cv2.resize(bg,None,fx=s,fy=s,interpolation=cv2.INTER_AREA)
    L=cv2.cvtColor(bg,cv2.COLOR_BGR2LAB)[...,0].astype(np.float32)
    loc=cv2.medianBlur(L.astype(np.uint8),31).astype(np.float32)
    busy=(np.abs(L-loc)>35)
    busy=cv2.morphologyEx(busy.astype(np.uint8),cv2.MORPH_OPEN,np.ones((2,2),np.uint8))
    hits=[]
    for i,it in enumerate(items):
        if masks[i] is None or it[0]!='text': continue
        b,m=masks[i]
        mm_=cv2.dilate(m.astype(np.uint8),np.ones((5,5),np.uint8))
        sub=busy[b[1]-2:b[3]-2+mm_.shape[0]-(b[3]-b[1])+ (b[3]-b[1]), b[0]:b[0]+mm_.shape[1]] if False else busy[b[1]:b[3],b[0]:b[2]]
        n=int((sub[:m.shape[0],:m.shape[1]] & mm_[:sub.shape[0],:sub.shape[1]]).sum())
        frac=n/max(1,m.sum())
        if frac>0.03: hits.append((labels[i],n,round(frac,3)))
    # inside trim / safe area
    trim=(5*K,5*K,(pw-5)*K,(ph-5)*K)
    edge=[]
    for i,it in enumerate(items):
        if masks[i] is None: continue
        b,_=masks[i]
        d=min(b[0]-trim[0],b[1]-trim[1],trim[2]-b[2],trim[3]-b[3])/K
        edge.append((labels[i],round(d,2)))
    edge.sort(key=lambda z:z[1])
    minsize=None
    sizes=[(it[3][2],round(it[3][4],2)) for it in items if it[0]=='text']
    sizes.sort(key=lambda z:z[1])
    report[side]=dict(n_items=len(items),ink_overlaps=over,text_on_busy_raster=hits,closest_to_trim_mm=edge[:6],smallest_pt=sizes[:6])
    print(f'== {side}: {len(items)} items | ink overlaps: {len(over)} | text over busy raster: {len(hits)}')
    for o in over: print('   OVERLAP',o)
    for h in hits: print('   BUSY',h)
    print('   closest to trim (mm):',edge[:5])
    print('   smallest sizes (pt):',sizes[:5])
json.dump(report,open('qa_overlap.json','w'),indent=1,default=str)
