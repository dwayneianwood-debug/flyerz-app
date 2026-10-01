import sys, pickle, numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0,'/workspace/medwork')
from fonts import pool; from match import render, deskew, soft; from groups import group, CANDS
P={l:(p,k) for l,p,k in pool()}
def fitted(L,lbl,H):
    p,kind=P[lbl]; om,ang=deskew(L); oh,ow=om.shape; text=L['text']; n=len(text)
    a,bb=render(p,text); k=oh/a.shape[0]; w0=a.shape[1]*k
    t=(ow-w0)/(n-1)/k if n>1 else 0
    if kind!='script' and n>1 and t>-0.08*120:
        a,_=render(p,text,track=t)
    img=cv2.resize(a,(max(1,int(ow*H/oh)),H),interpolation=cv2.INTER_AREA)
    tgt=cv2.resize(om,(img.shape[1],H),interpolation=cv2.INTER_AREA)
    return 255-img, soft(tgt,255-(255-img)) , ow/w0
def make(side, ids, grp, out, H=44):
    meta={L['id']:L for L in pickle.load(open(f'meta_{side}.pkl','rb'))}
    src=Image.open(f'/workspace/medella_out/src_{side}.png').convert('L')
    cands=CANDS[grp]; rows=[]
    for i in ids:
        L=meta[f'{side}:{i}']; om,_=deskew(L)
        orig=cv2.resize(255-om,(max(1,int(om.shape[1]*H/om.shape[0])),H),interpolation=cv2.INTER_AREA)
        row=[('ORIGINAL',orig)]
        for c in cands:
            img,sc,sx=fitted(L,c,H); row.append((f'{c} ({sc:.2f})',img))
        rows.append(row)
    W=max(r[0][1].shape[1] for r in rows)+10; cols=len(rows[0])
    per=4; nrow=int(np.ceil(cols/per))
    sheet=Image.new('L',(W*per, (H+18)*nrow*len(rows)+10),255); d=ImageDraw.Draw(sheet)
    y=0
    for r in rows:
        for j,(lab,img) in enumerate(r):
            cx=(j%per)*W; cy=y+(j//per)*(H+18)
            d.text((cx+2,cy),lab,fill=0); sheet.paste(Image.fromarray(img),(cx,cy+14))
        y+=(H+18)*nrow
    sheet.save(out); print(out,sheet.size)
if __name__=='__main__':
    make('flyer_front',[0,7,50],'caps','sheet_caps.png')
