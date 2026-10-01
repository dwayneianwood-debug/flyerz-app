import json,sys,numpy as np,unicodedata,difflib,cv2
from PIL import Image
Image.MAX_IMAGE_PIXELS=None
from rapidocr import RapidOCR
import pymupdf
sys.path.insert(0,'/workspace/medwork')
from spec import lines
eng=RapidOCR()
MM=25.4; DPI=600
SIDE={'flyer_front':(205/1536,((158-1024*205/1536)/2,7.5),'h_fly-1.png','flyer_A5_front_back_press.pdf',0),
      'flyer_back':(148/1054,(5,(220-1492*148/1054)/2),'h_fly-2.png','flyer_A5_front_back_press.pdf',1),
      'card_front':(50/992,((100-1586*50/992)/2,5),'h_card-1.png','business_card_front_back_press.pdf',0)}
SRC={k:f'/workspace/medella_out/src_{k}.png' for k in SIDE}
def norm(t): return unicodedata.normalize('NFC',t.replace('\u2019',"'").replace('\u2013','-').replace('Ｅ','E').replace('Ｔ','T')).strip()
def sq(t): return norm(t).replace(' ','')
def cover(exp,got):
    e,g=sq(exp),sq(got)
    if not e: return 1.0
    sm=difflib.SequenceMatcher(None,e,g,autojunk=False)
    return sum(b.size for b in sm.get_matching_blocks())/len(e)
def quadcrop(img,q,padv=0.10,padh=0.6,out_h=80):
    q=np.array(q,np.float32); h=(np.linalg.norm(q[3]-q[0])+np.linalg.norm(q[2]-q[1]))/2; w=(np.linalg.norm(q[1]-q[0])+np.linalg.norm(q[2]-q[3]))/2
    ux=(q[1]-q[0])/np.linalg.norm(q[1]-q[0]); uy=(q[3]-q[0])/np.linalg.norm(q[3]-q[0])
    p=h*padv; px=h*padh
    src=np.array([q[0]-ux*px-uy*p,q[1]+ux*px-uy*p,q[2]+ux*px+uy*p,q[3]-ux*px+uy*p],np.float32)
    S=out_h/(h+2*p); W=int((w+2*px)*S); H=int(out_h)
    M=cv2.getPerspectiveTransform(src,np.array([[0,0],[W,0],[W,H],[0,H]],np.float32))
    c=cv2.warpPerspective(img,M,(W,H),flags=cv2.INTER_AREA if S<1 else cv2.INTER_CUBIC,borderMode=cv2.BORDER_REPLICATE)
    return cv2.copyMakeBorder(c,24,24,24,24,cv2.BORDER_REPLICATE)
def rec(c):
    r=eng(c)
    if r.txts is None: return ''
    # keep boxes whose vertical centre is near crop centre (drop neighbour-line fragments)
    H=c.shape[0]; keep=[]
    for b,t in zip(r.boxes,r.txts):
        b=np.array(b); cy=b[:,1].mean()
        if abs(cy-H/2)<H*0.28: keep.append((b[:,0].min(),t))
    return ' '.join(t for _,t in sorted(keep))
def best(img,q):
    outs=[rec(quadcrop(img,q,pv,ph)) for pv,ph in ((0.10,0.6),(0.22,0.8),(0.04,0.4))]
    return outs
rep={}
for side,(s,off,png,pdf,pg) in SIDE.items():
    k=DPI/MM; R=cv2.imread(png); O=cv2.imread(SRC[side])
    res=[]
    for l in lines(side):
        exp=norm(l['text']); q=np.array(l['quad'],float); qr=(np.array(off)+q*s)*k
        gr=best(R,qr); go=best(O,q)
        cr=max(cover(exp,g) for g in gr); co=max(cover(exp,g) for g in go)
        gbest=max(gr,key=lambda g:cover(exp,g))
        res.append(dict(id=l['id'],expected=exp,ocr_render=gbest,cov_render=round(cr,3),cov_orig=round(co,3)))
    rep[side]=res
json.dump(rep,open('qa_ocr3.json','w'),indent=1,ensure_ascii=False)
# card back: spans from pdf
d=pymupdf.open('/workspace/medella_vector/business_card_front_back_press.pdf'); p=d[1]
R=cv2.imread('h_card-2.png'); k=DPI/72; res=[]
for b in p.get_text('dict')['blocks']:
    for l in b.get('lines',[]):
        t=norm(''.join(s['text'] for s in l['spans']));
        if not t.strip(): continue
        x0,y0,x1,y1=l['bbox']; q=np.array([[x0,y0],[x1,y0],[x1,y1],[x0,y1]])*k
        gr=best(R,q); cr=max(cover(t,g) for g in gr)
        res.append(dict(id='card_back',expected=t,ocr_render=max(gr,key=lambda g:cover(t,g)),cov_render=round(cr,3),cov_orig=None))
rep['card_back']=res
json.dump(rep,open('qa_ocr3.json','w'),indent=1,ensure_ascii=False)
for side,res in rep.items():
    n=len(res); ok=[r for r in res if r['cov_render']>=0.999]; wk=[r for r in res if r['cov_render']<0.999]
    print(f'== {side}: {n} lines | render-OCR full coverage {len(ok)}/{n}')
    for r in wk: print('   ',r['id'],r['cov_render'],'orig',r['cov_orig'],'| exp:',r['expected'],'| got:',r['ocr_render'])
