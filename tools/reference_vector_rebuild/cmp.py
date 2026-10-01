import sys,json,numpy as np,cv2
sys.path.insert(0,'/workspace/medwork')
from spec import lines
MM=25.4
SIDE={'flyer_front':(205/1536,((158-1024*205/1536)/2,7.5),'q_fly-1.png'),'flyer_back':(148/1054,(5,(220-1492*148/1054)/2),'q_fly-2.png'),'card_front':(50/992,((100-1586*50/992)/2,5),'q_card-1.png')}
SRC={'flyer_front':'/workspace/medella_out/src_flyer_front.png','flyer_back':'/workspace/medella_out/src_flyer_back.png','card_front':'/workspace/medella_out/src_card_front.png'}
def region(side,box,out,scale=1.0,dpi=300):
    s,off,png=SIDE[side]; k=dpi/MM
    R=cv2.imread(png) if dpi==300 else None
    O=cv2.imread(SRC[side])
    x0,y0,x1,y1=box
    o=O[int(y0):int(y1),int(x0):int(x1)]
    X0,Y0,X1,Y1=[int(round((off[i%2]+v*s)*k)) for i,v in enumerate(box)]
    r=R[Y0:Y1,X0:X1]
    o=cv2.resize(o,(r.shape[1],r.shape[0]),interpolation=cv2.INTER_CUBIC)
    img=np.vstack([o,np.full((6,r.shape[1],3),(0,0,255),np.uint8),r])
    if scale!=1: img=cv2.resize(img,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC)
    cv2.imwrite(out,img)
if __name__=='__main__':
    side=sys.argv[1]; box=list(map(float,sys.argv[2:6])); out=sys.argv[6]; sc=float(sys.argv[7]) if len(sys.argv)>7 else 1
    region(side,box,out,sc)
