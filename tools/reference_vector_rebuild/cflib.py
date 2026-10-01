import numpy as np
def feats(L):
    L=L.reshape(-1,3)/255.0; l,a,b=L[:,0],L[:,1],L[:,2]
    return np.stack([np.ones_like(l),l,a,b,l*l,a*a,b*b,l*a,l*b,a*b,l*l*l,a*a*a,b*b*b],1)
def warm_mask(L):
    a=L[...,1]-128; b=L[...,2]-128
    return np.clip((b-12)/12,0,1)*np.clip((a+4)/6,0,1)*np.clip((40-a)/8,0,1)
