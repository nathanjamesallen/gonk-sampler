import json, sys, os, numpy as np, soundfile as sf, librosa, matplotlib
matplotlib.use('Agg'); import matplotlib.pyplot as plt
run=sys.argv[1]; kit=json.load(open(f'{run}/kit.json'))['pads']
sd=sys.argv[2] if len(sys.argv)>2 else f'{run}/slices'
rr=max(len(p['variants']) for p in kit)
fig,ax=plt.subplots(len(kit),rr,figsize=(rr*3.2,len(kit)*1.25))
for i,p in enumerate(kit):
    for j in range(rr):
        a=ax[i,j]; a.set_xticks([]); a.set_yticks([])
        if j>=len(p['variants']): a.axis('off'); continue
        y,sr=sf.read(os.path.join(sd,p['variants'][j]),always_2d=True); m=y.mean(1)
        S=librosa.amplitude_to_db(np.abs(librosa.stft(m,n_fft=1024,hop_length=128))+1e-9,ref=1.0)
        a.imshow(S[:300],origin='lower',aspect='auto',vmin=-80,vmax=20,cmap='magma',extent=[0,len(m)/sr,0,300])
        a.plot(np.linspace(0,len(m)/sr,len(m)),150+140*m/ max(1e-6,np.abs(m).max()),color='c',lw=.3)
        a.set_xlim(0,1.0)
        if j==0: a.set_ylabel(f"{p['note']} {p['name']}",rotation=0,ha='right',fontsize=8)
        a.set_title(f"{len(m)/sr:.2f}s pk{20*np.log10(np.abs(y).max()):.0f}",fontsize=6,pad=1)
plt.tight_layout(pad=.2); plt.savefig(f'{run}/contact.png',dpi=55)
