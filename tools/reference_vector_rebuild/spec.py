import json, numpy as np
from PIL import Image
d=json.load(open('/workspace/medwork/ocr.json'))
DROP={'flyer_front':{2,15,91},'flyer_back':{12,64},'card_front':set()}
X1={'flyer_front':{12:345,72:220,93:637,17:695},'card_front':{7:733}}
TEXT={'flyer_front':{12:'live your best life',72:'live your best life',93:'See your health, live your best life',17:'Empowering you to take charge of your health.'},
      'card_front':{7:'See your health, live your best life'}}
def lines(side):
    out=[]
    for i,l in enumerate(d[side]['lines']):
        if i in DROP.get(side,()): continue
        t=TEXT.get(side,{}).get(i,l['text']).strip()
        q=np.array(l['quad'],float)
        b=list(l['box'])
        if i in X1.get(side,{}):
            nx=X1[side][i]; b[2]=nx
            q[1,0]=min(q[1,0],nx); q[2,0]=min(q[2,0],nx)
        if side=='flyer_front' and i==17:
            q[0,1]=max(q[0,1],560); q[1,1]=max(q[1,1],560); b[1]=560
        if side=='flyer_back' and i==1:
            q=np.array([[190,120],[835,120],[835,222],[190,222]],float); b=[190,120,835,222]
        if side=='card_front' and i==8:
            q=np.array([[1072,766],[1440,766],[1440,850],[1072,850]],float); b=[1072,766,1440,850]
        if side=='card_front' and i==0:
            q=np.array([[985,180],[1432,180],[1432,300],[985,300]],float); b=[985,180,1432,300]
        out.append({'id':f'{side}:{i}','text':t,'quad':q.tolist(),'box':b})
    if side=='flyer_front':
        x0,y0,x1,y1=701,678,720,705
        out.append({'id':'flyer_front:num1','text':'1','quad':[[x0,y0],[x1,y0],[x1,y1],[x0,y1]],'box':[x0,y0,x1,y1]})
    return out
if __name__=='__main__':
    for s in ['flyer_front','flyer_back','card_front']:
        L=lines(s); print(s,len(L)); 
        if s=='flyer_front': print(L[-1])
