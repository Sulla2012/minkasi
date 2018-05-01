import numpy 
import ctypes
import time
import pyfftw
import pyfits
import astropy
from astropy import wcs
from astropy.io import fits

try:
    from mpi4py import MPI
    comm=MPI.COMM_WORLD
    myrank = comm.Get_rank()
    nproc=comm.Get_size()
    have_mpi=True
except:
    have_mpi=False
    myrank=0
    nproc=1

mylib=ctypes.cdll.LoadLibrary("libminkasi.so")
tod2map_simple_c=mylib.tod2map_simple
tod2map_simple_c.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_void_p]

tod2map_omp_c=mylib.tod2map_omp
tod2map_omp_c.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_void_p,ctypes.c_int]

map2tod_simple_c=mylib.map2tod_simple
map2tod_simple_c.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_void_p,ctypes.c_int]

map2tod_omp_c=mylib.map2tod_omp
map2tod_omp_c.argtypes=[ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,ctypes.c_int]


set_nthread_c=mylib.set_nthread
set_nthread_c.argtypes=[ctypes.c_int]

get_nthread_c=mylib.get_nthread
get_nthread_c.argtypes=[ctypes.c_void_p]

fill_isobeta_c=mylib.fill_isobeta
fill_isobeta_c.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_int]

fill_gauss_src_c=mylib.fill_gauss_src
fill_gauss_src_c.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_int]




def report_mpi():
    if have_mpi:
        print 'myrank is ',myrank,' out of ',nproc
    else:
        print 'mpi not found'

def invsafe(mat,thresh=1e-14):
    u,s,v=numpy.linalg.svd(mat,0)
    ii=numpy.abs(s)<thresh*s.max()
    #print ii
    s_inv=1/s
    s_inv[ii]=0
    tmp=numpy.dot(numpy.diag(s_inv),u.transpose())
    return numpy.dot(v.transpose(),tmp)

def tod2map_simple(map,dat,ipix):
    ndet=dat.shape[0]
    ndata=dat.shape[1]
    tod2map_simple_c(map.ctypes.data,dat.ctypes.data,ndet,ndata,ipix.ctypes.data)

def tod2map_omp(map,dat,ipix):
    ndet=dat.shape[0]
    ndata=dat.shape[1]
    tod2map_omp_c(map.ctypes.data,dat.ctypes.data,ndet,ndata,ipix.ctypes.data,map.size)
    

def map2tod(dat,map,ipix,do_add=False,do_omp=True):
    ndet=dat.shape[0]
    ndata=dat.shape[1]
    if do_omp:
        map2tod_omp_c(dat.ctypes.data, map.ctypes.data, ndet, ndata, ipix.ctypes.data, do_add)
    else:
        map2tod_simple_c(dat.ctypes.data,map.ctypes.data,ndet,ndata,ipix.ctypes.data,do_add)
    

def set_nthread(nthread):
    set_nthread_c(nthread)

def get_nthread():
    nthread=numpy.zeros([1,1],dtype='int32')
    get_nthread_c(nthread.ctypes.data)
    return nthread[0,0]

def find_spikes(dat,inner=1,outer=10,rad=0.25,thresh=8,pad=2):
    #find spikes in a block of timestreams
    n=dat.shape[1];
    ndet=dat.shape[0]
    x=numpy.arange(n);
    filt1=numpy.exp(-0.5*x**2/inner**2)
    filt1=filt1+numpy.exp(-0.5*(x-n)**2/inner**2);
    filt1=filt1/filt1.sum()

    filt2=numpy.exp(-0.5*x**2/outer**2)
    filt2=filt2+numpy.exp(-0.5*(x-n)**2/outer**2);
    filt2=filt2/filt2.sum()
    
    filt=filt1-filt2 #make a filter that is the difference of two Gaussians, one narrow, one wide
    filtft=numpy.fft.rfft(filt)
    datft=numpy.fft.rfft(dat,axis=1)
    datfilt=numpy.fft.irfft(filtft*datft,axis=1,n=n)
    jumps=[None]*ndet
    mystd=numpy.median(numpy.abs(datfilt),axis=1)
    for i in range(ndet):
        while numpy.max(numpy.abs(datfilt[i,:]))>thresh*mystd[i]:
            ind=numpy.argmax(numpy.abs(datfilt[i,:]))
            if jumps[i] is None:
                jumps[i]=[ind]
            else:
                jumps[i].append(ind)
            datfilt[i,ind]=0
    return jumps,datfilt
            
        
    
    return mystd


def find_jumps(dat,width=10,pad=2,thresh=10,rat=0.5):
    #find jumps in a block of timestreams, preferably with the common mode removed
    #width is width in pixels to average over when looking for a jump
    #pad is the length in units of width to mask at beginning/end of timestream
    #thresh is threshold in units of filtered data median absolute deviation to qualify as a jump
    #rat is the ratio of largest neighboring opposite-sign jump to the found jump.  If
    #  there is an opposite-sign jump nearby, the jump finder has probably just picked up a spike.
    n=dat.shape[1]
    ndet=dat.shape[0]

    #make a filter template that is a gaussian with sigma with, sign-flipped in the center
    #so, positive half-gaussian starting from zero, and negative half-gaussian at the end
    x=numpy.arange(n)
    myfilt=numpy.exp(-0.5*x**2/width**2)
    myfilt=myfilt-numpy.exp( (-0.5*(x-n)**2/width**2))
    fac=numpy.abs(myfilt).sum()/2.0
    myfilt=myfilt/fac

    dat_filt=numpy.fft.rfft(dat,axis=1)

    myfilt_ft=numpy.fft.rfft(myfilt)
    dat_filt=dat_filt*numpy.repeat([myfilt_ft],ndet,axis=0)
    dat_filt=numpy.fft.irfft(dat_filt,axis=1,n=n)
    dat_filt_org=dat_filt.copy()

    print dat_filt.shape
    dat_filt[:,0:pad*width]=0
    dat_filt[:,-pad*width:]=0
    det_thresh=thresh*numpy.median(numpy.abs(dat_filt),axis=1)
    dat_dejump=dat.copy()
    jumps=[None]*ndet
    print 'have filtered data, now searching for jumps'
    for i in range(ndet):
        while numpy.max(numpy.abs(dat_filt[i,:]))>det_thresh[i]:            
            ind=numpy.argmax(numpy.abs(dat_filt[i,:]))+1 #+1 seems to be the right index to use
            imin=ind-width
            if imin<0:
                imin=0
            imax=ind+width
            if imax>n:
                imax=n
            val=dat_filt[i,ind]
            if val>0:
                val2=numpy.min(dat_filt[i,imin:imax])
            else:
                val2=numpy.max(dat_filt[i,imin:imax])
            
            
            print 'found jump on detector ',i,' at sample ',ind
            if numpy.abs(val2/val)>rat:
                print 'I think this is a spike due to ratio ',numpy.abs(val2/val)
            else:
                if jumps[i] is None:
                    jumps[i]=[ind]
                else:
                    jumps[i].append(ind)
            #independent of if we think it is a spike or a jump, zap that stretch of the data
            dat_dejump[i,ind:]=dat_dejump[i,ind:]+dat_filt[i,ind]
            dat_filt[i,ind-pad*width:ind+pad*width]=0
        if not(jumps[i] is None):
            jumps[i]=numpy.sort(jumps[i])
    #return dat_dejump,jumps,dat_filt_org
    return jumps

def fit_jumps_from_cm(dat,jumps,cm,cm_order=1,poly_order=1):
    jump_vals=jumps[:]
    ndet=len(jumps)
    n=dat.shape[1]
    x=numpy.linspace(-1,1,n)
    m1=numpy.polynomial.legendre.legvander(x,poly_order)
    m2=numpy.polynomial.legendre.legvander(x,cm_order-1)
    for i in range(cm_order):
        m2[:,i]=m2[:,i]*cm
    mat=numpy.append(m1,m2,axis=1)
    np=mat.shape[1]

    dat_dejump=dat.copy()
    for i in range(ndet):
        if not(jumps[i] is None):
            njump=len(jumps[i])
            segs=numpy.append(jumps[i],n)
            print 'working on detector ',i,' who has ', len(jumps[i]),' jumps with segments ',segs
            mm=numpy.zeros([n,np+njump])
            mm[:,:np]=mat
            for j in range(njump):
                mm[segs[j]:segs[j+1],j+np]=1.0
            lhs=numpy.dot(mm.transpose(),mm)
            #print lhs
            rhs=numpy.dot(mm.transpose(),dat[i,:].transpose())
            lhs_inv=numpy.linalg.inv(lhs)
            fitp=numpy.dot(lhs_inv,rhs)
            jump_vals[i]=fitp[np:]
            jump_pred=numpy.dot(mm[:,np:],fitp[np:])
            dat_dejump[i,:]=dat_dejump[i,:]-jump_pred


    return dat_dejump
            

    #for i in range(ndet):


def get_type(nbyte):
    if nbyte==8:
        return numpy.dtype('float64')
    if nbyte==4:
        return numpy.dtype('float32')
    if nbyte==-4:
        return numpy.dtype('int32')
    if nbyte==-8:
        return numpy.dtype('int64')
    if nbyte==1:
        return numpy.dtype('str')
    print 'Unsupported nbyte ' + repr(nbyte) + ' in get_type'
    return None

def read_octave_struct(fname):
    f=open(fname)
    nkey=numpy.fromfile(f,'int32',1)[0]
    #print 'nkey is ' + repr(nkey)
    dat={}
    for i in range(nkey):
        key=f.readline().strip()
        #print 'key is ' + key
        ndim=numpy.fromfile(f,'int32',1)[0]
        dims=numpy.fromfile(f,'int32',ndim)
        dims=numpy.flipud(dims)
        #print 'Dimensions of ' + key + ' are ' + repr(dims)
        nbyte=numpy.fromfile(f,'int32',1)[0]
        #print 'nbyte is ' + repr(nbyte)
        dtype=get_type(nbyte)
        tmp=numpy.fromfile(f,dtype,dims.prod())
        dat[key]=numpy.reshape(tmp,dims)
    f.close()
    return dat



def nsphere_vol(np):
    iseven=(np%2)==0
    if iseven:
        nn=np/2
        vol=(numpy.pi**nn)/numpy.prod(numpy.arange(1,nn+1))
    else:
        nn=(np-1)/2
        vol=2**(nn+1)*numpy.pi**nn/numpy.prod(numpy.arange(1,np+1,2))
    return vol


def _prime_loop(ln,lp,icur,lcur,vals):
    facs=numpy.arange(lcur,ln+1e-3,lp[0])
    if len(lp)==1:
        nfac=len(facs)
        if (nfac>0):
            vals[icur:(icur+nfac)]=facs
            icur=icur+nfac
            #print 2**vals[:icur]
        else:
            print 'bad facs came from ' + repr([2**lcur,2**ln,2**lp[0]])
        #print icur
        return icur
    else:
        facs=numpy.arange(lcur,ln,lp[0])
        for fac in facs:
            icur=_prime_loop(ln,lp[1:],icur,fac,vals)
        return icur
    print 'I don''t think I should have gotten here.'
    return icur
                             
        

def find_good_fft_lens(n,primes=[2,3,5,7]):
    lmax=numpy.log(n+0.5)
    np=len(primes)
    vol=nsphere_vol(np)

    r=numpy.log2(n+0.5)
    lp=numpy.log2(primes)
    npoint_max=(vol/2**np)*numpy.prod(r/lp)+30 #add a bit just to make sure we don't act up for small n
    #print 'npoint max is ',npoint max
    npoint_max=numpy.int(npoint_max)

    #vals=numpy.zeros(npoint_max,dtype='int')
    vals=numpy.zeros(npoint_max)
    icur=0
    icur=_prime_loop(r,lp,icur,0.0,vals)
    assert(icur<=npoint_max)
    myvals=numpy.asarray(numpy.round(2**vals[:icur]),dtype='int')
    myvals=numpy.sort(myvals)
    return myvals
    
    

def _linfit_2mat(dat,mat1,mat2):
    np1=mat1.shape[1]
    np2=mat2.shape[1]
    mm=numpy.append(mat1,mat2,axis=1)
    lhs=numpy.dot(mm.transpose(),mm)
    rhs=numpy.dot(mm.transpose(),dat)
    lhs_inv=numpy.linalg.inv(lhs)
    fitp=numpy.dot(lhs_inv,rhs)
    fitp1=fitp[0:np1].copy()
    fitp2=fitp[np1:].copy()
    assert(len(fitp2)==np2)
    return fitp1,fitp2



def smooth_spectra(spec,fwhm):
    nspec=spec.shape[0]
    n=spec.shape[1]

    x=numpy.arange(n)
    sig=fwhm/numpy.sqrt(8*numpy.log(2))
    to_conv=numpy.exp(-0.5*(x/sig)**2)
    tot=to_conv[0]+to_conv[-1]+2*to_conv[1:-1].sum() #r2r normalization
    to_conv=to_conv/tot
    to_conv_ft=pyfftw.fft_r2r(to_conv)
    xtrans=pyfftw.fft_r2r(spec)
    for i in range(nspec):
        xtrans[i,:]=xtrans[i,:]*to_conv_ft
    #return pyfftw.fft_r2r(xtrans)/(2*(xtrans.shape[1]-1)),to_conv
    return xtrans,to_conv_ft
def smooth_many_vecs(vecs,fwhm=20):
    n=vecs.shape[1]
    nvec=vecs.shape[0]
    x=numpy.arange(n)
    sig=fwhm/numpy.sqrt(8*numpy.log(2))
    to_conv=numpy.exp(-0.5*(x/sig)**2)
    tot=to_conv[0]+to_conv[-1]+2*to_conv[1:-1].sum() #r2r normalization
    to_conv=to_conv/tot
    to_conv_ft=pyfftw.fft_r2r(to_conv)
    xtrans=pyfftw.fft_r2r(vecs)
    for i in range(nvec):
        xtrans[i,:]=xtrans[i,:]*to_conv_ft
    back=pyfftw.fft_r2r(xtrans)
    return back/(2*(n-1))
def smooth_vec(vec,fwhm=20):
    n=vec.size
    x=numpy.arange(n)
    sig=fwhm/numpy.sqrt(8*numpy.log(2))
    to_conv=numpy.exp(-0.5*(x/sig)**2)
    tot=to_conv[0]+to_conv[-1]+2*to_conv[1:-1].sum() #r2r normalization
    to_conv=to_conv/tot
    to_conv_ft=pyfftw.fft_r2r(to_conv)
    xtrans=pyfftw.fft_r2r(vec)
    back=pyfftw.fft_r2r(xtrans*to_conv_ft)
    return back/2.0/(n-1)


def fit_cm_plus_poly(dat,ord=2,cm_ord=1,niter=2,medsub=False,full_out=False):
    n=dat.shape[1]
    ndet=dat.shape[0]
    if medsub:
        med=numpy.median(dat,axis=1)        
        dat=dat-numpy.repeat([med],n,axis=0).transpose()
        
        

    xx=numpy.arange(n)+0.0
    xx=xx-xx.mean()
    xx=xx/xx.max()

    pmat=numpy.polynomial.legendre.legvander(xx,ord)
    cm_pmat=numpy.polynomial.legendre.legvander(xx,cm_ord-1)
    calfacs=numpy.ones(ndet)*1.0
    dd=dat.copy()
    for i in range(1,niter):
        for j in range(ndet):
            dd[j,:]/=calfacs[j]
            
        cm=numpy.median(dd,axis=0)
        cm_mat=numpy.zeros(cm_pmat.shape)
        for i in range(cm_mat.shape[1]):
            cm_mat[:,i]=cm_pmat[:,i]*cm
        fitp_p,fitp_cm=_linfit_2mat(dat.transpose(),pmat,cm_mat)
        pred1=numpy.dot(pmat,fitp_p).transpose()
        pred2=numpy.dot(cm_mat,fitp_cm).transpose()
        pred=pred1+pred2
        dd=dat-pred1
        
    if full_out:
        return dd,pred2,cm #if requested, return the modelled CM as well
    return dd


def __run_pcg_old(b,x0,tods,mapset,precon):
    Ax=mapset.dot(x0)

    r=b-Ax
    z=precon*r
    p=z.copy()
    k=0
    zr=r.dot(z)
    x=x0.copy()
    for iter in range(25):
        print iter,zr
        Ap=mapset.dot(p)
        pAp=p.dot(Ap)
        alpha=zr/pAp

        x_new=x+p*alpha
        r_new=r-Ap*alpha 
        z_new=precon*r_new
        zr_new=r_new.dot(z_new)
        beta=zr_new/zr
        p_new=z_new+p*beta
        
        p=p_new
        z=z_new
        r=r_new
        zr=zr_new
        x=x_new
    return x

def run_pcg(b,x0,tods,precon=None,maxiter=25):
    t1=time.time()
    Ax=tods.dot(x0)

    try:
        r=b.copy()
        r.axpy(Ax,-1)
    except:
        r=b-Ax
    if not(precon is None):
        z=precon*r
    else:
        z=r.copy()
    p=z.copy()
    k=0.0

    zr=r.dot(z)
    x=x0.copy()
    t2=time.time()
    for iter in range(maxiter):
        if myrank==0:
            print iter,zr,t2-t1
        t1=time.time()
        Ap=tods.dot(p)
        pAp=p.dot(Ap)
        alpha=zr/pAp
        try:
            x_new=x.copy()
            x_new.axpy(p,alpha)
        except:
            x_new=x+p*alpha

        try:
            r_new=r.copy()
            r_new.axpy(Ap,-alpha)
        except:
            r_new=r-Ap*alpha
        if not(precon is None):
            z_new=precon*r_new
        else:
            z_new=r_new.copy()
        zr_new=r_new.dot(z_new)
        beta=zr_new/zr
        try:
            p_new=z_new.copy()
            p_new.axpy(p,beta)
        except:
            p_new=z_new+p*beta
        
        p=p_new
        z=z_new
        r=r_new
        zr=zr_new
        x=x_new
        t2=time.time()
    return x

def apply_noise(tod,dat=None):
    if dat is None:
        dat=tod['dat_calib']
    dat_rot=numpy.dot(tod['v'],dat)
    datft=pyfftw.fft_r2r(dat_rot)
    nn=datft.shape[1]
    datft=datft*tod['mywt'][:,0:nn]
    dat_rot=pyfftw.fft_r2r(datft)
    dat=numpy.dot(tod['v'].transpose(),dat_rot)
    return dat



class null_precon:
    def __init__(self):
        self.isnull=True
    def __add__(self,val):
        return val
    def __mul__(self,val):
        return val

class Mapset:
    def __init__(self):
        self.nmap=0
        self.maps=[]
    def add_map(self,map):
        self.maps.append(map.copy())
        self.nmap=self.nmap+1
    def clear(self):
        for i in range(self.nmap):
            self.maps[i].clear()
    def copy(self):
        new_mapset=Mapset()
        for i in range(self.nmap):
            new_mapset.add_map(self.maps[i].copy())
        return new_mapset
    def dot(self,mapset):
        tot=0.0
        for i in range(self.nmap):
            tot=tot+self.maps[i].dot(mapset.maps[i])
        return tot
    def axpy(self,mapset,a):
        for i in range(self.nmap):
            self.maps[i].axpy(mapset.maps[i],a)
    def __add__(self,mapset):
        mm=self.copy()
        mm.axpy(mapset,1.0)
        return mm

    def __sub__(self,mapset):
        mm=self.copy()
        mm.axpy(mapset,-1.0)
        return mm
    def __mul__(self,mapset):
        mm=self.copy()
        for i in range(self.nmap):
            mm.maps[i]=self.maps[i]*mapset.maps[i]
        return mm
    def mpi_reduce(self):
        if have_mpi:
            for map in self.maps:
                map.mpi_reduce()
class Cuts:
    def __init__(self,tod):
        self.tag=tod.info['tag']
        self.ndet=tod.info['dat_calib'].shape[0]
        self.cuts=[None]*self.ndet

class CutsVec:
    def __init__(self,todvec):
        self.ntod=todvec.ntod
        self.cuts=[None]*self.ntod
        for tod in todvec.tods:
            self.cuts[tod.info['tag']]=Cuts(tod)
            
class SkyMap:
    def __init__(self,lims,pixsize,proj='CAR',pad=2,primes=None):
        self.wcs=get_wcs(lims,pixsize,proj)
        corners=numpy.zeros([4,2])
        corners[0,:]=[lims[0],lims[2]]
        corners[1,:]=[lims[0],lims[3]]
        corners[2,:]=[lims[1],lims[2]]
        corners[3,:]=[lims[1],lims[3]]
        pix_corners=self.wcs.wcs_world2pix(corners*180/numpy.pi,1)
        #print pix_corners
        if pix_corners.min()<0.5:
            print 'corners seem to have gone negative in SkyMap projection.  not good, you may want to check this.'
        nx=(pix_corners[:,0].max()+pad)
        ny=(pix_corners[:,1].max()+pad)
        #print nx,ny
        nx=int(nx)
        ny=int(ny)
        if not(primes is None):
            lens=find_good_fft_lens(2*(nx+ny),primes)
            #print 'nx and ny initially are ',nx,ny
            nx=lens[lens>=nx].min()
            ny=lens[lens>=ny].min()
            #print 'small prime nx and ny are now ',nx,ny
            self.primes=primes[:]
        else:
            self.primes=None
        self.nx=nx
        self.ny=ny
        self.lims=lims
        self.pixsize=pixsize
        self.map=numpy.zeros([nx,ny])
        self.proj=proj
        self.pad=pad

    def copy(self):
        newmap=SkyMap(self.lims,self.pixsize,self.proj,self.pad,self.primes)
        newmap.map[:]=self.map[:]
        return newmap
    def clear(self):
        self.map[:]=0
    def axpy(self,map,a):
        self.map[:]=self.map[:]+a*map.map[:]
    def assign(self,arr):
        assert(arr.shape[0]==self.nx)
        assert(arr.shape[1]==self.ny)
        self.map[:,:]=arr
    def get_pix(self,tod):
        ndet=tod.info['dx'].shape[0]
        nsamp=tod.info['dx'].shape[1]
        nn=ndet*nsamp
        coords=numpy.zeros([nn,2])
        coords[:,0]=numpy.reshape(tod.info['dx']*180/numpy.pi,nn)
        coords[:,1]=numpy.reshape(tod.info['dy']*180/numpy.pi,nn)
        #print coords.shape
        pix=self.wcs.wcs_world2pix(coords,1)
        #print pix.shape
        xpix=numpy.reshape(pix[:,0],[ndet,nsamp])-1  #-1 is to go between unit offset in FITS and zero offset in python
        ypix=numpy.reshape(pix[:,1],[ndet,nsamp])-1  
        xpix=numpy.round(xpix)
        ypix=numpy.round(ypix)
        ipix=numpy.asarray(xpix*self.ny+ypix,dtype='int32')
        return ipix
    def map2tod(self,tod,dat,do_add=True,do_omp=True):
        map2tod(dat,self.map,tod.info['ipix'],do_add,do_omp)

    def tod2map(self,tod,dat,do_add=True,do_omp=True):
        if do_add==False:
            self.clear()
        if do_omp:
            tod2map_omp(self.map,dat,tod.info['ipix'])
        else:
            tod2map_simple(self.map,dat,tod.info['ipix'])

    def r_th_maps(self):
        xvec=numpy.arange(self.nx)
        xvec=xvec-xvec.mean()        
        yvec=numpy.arange(self.ny)
        yvec=yvec-yvec.mean()
        ymat,xmat=numpy.meshgrid(yvec,xvec)
        rmat=numpy.sqrt(xmat**2+ymat**2)
        th=numpy.arctan2(xmat,ymat)
        return rmat,th
    def dot(self,map):
        tot=numpy.sum(self.map*map.map)
        return tot

    def write(self,fname='map.fits'):
        header=self.wcs.to_header()
        hdu=fits.PrimaryHDU(self.map,header=header)
        try:
            hdu.writeto(fname,overwrite=True)
        except:
            hdu.writeto(fname,clobber=True)
    def __mul__(self,map):
        new_map=self.copy()
        new_map.map[:]=self.map[:]*map.map[:]
        return new_map
    def mpi_reduce(self):
        if have_mpi:
            self.map=comm.allreduce(self.map)
class SkyMapCar:
    def __init__(self,lims,pixsize):
        try:
            self.lims=lims.copy()
        except:
            self.lims=lims[:]
        self.pixsize=pixsize
        self.cosdec=numpy.cos(0.5*(lims[2]+lims[3]))
        nx=numpy.int(numpy.ceil((lims[1]-lims[0])/pixsize*self.cosdec))
        ny=numpy.int(numpy.ceil((lims[3]-lims[2])/pixsize))
        self.nx=nx
        self.ny=ny
        self.npix=nx*ny
        self.map=numpy.zeros([nx,ny])
    def copy(self):
        mycopy=SkyMapCar(self.lims,self.pixsize)
        mycopy.map[:]=self.map[:]
        return mycopy
    def clear(self):
        self.map[:,:]=0

    def axpy(self,map,a):
        self.map[:]=self.map[:]+a*map.map[:]
        
    def assign(self,arr):
        assert(arr.shape[0]==self.nx)
        assert(arr.shape[1]==self.ny)
        self.map[:,:]=arr
    def get_pix(self,tod):
        xpix=numpy.round((tod.info['dx']-self.lims[0])*self.cosdec/self.pixsize)
        ypix=numpy.round((tod.info['dy']-self.lims[2])/self.pixsize)
        #ipix=numpy.asarray(ypix*self.nx+xpix,dtype='int32')
        ipix=numpy.asarray(xpix*self.ny+ypix,dtype='int32')
        return ipix
    def map2tod(self,tod,dat,do_add=True,do_omp=True):
        map2tod(dat,self.map,tod.info['ipix'],do_add,do_omp)

    def tod2map(self,tod,dat,do_add=True,do_omp=True):
        if do_add==False:
            self.clear()
        if do_omp:
            tod2map_omp(self.map,dat,tod.info['ipix'])
        else:
            tod2map_simple(self.map,dat,tod.info['ipix'])

    def r_th_maps(self):
        xvec=numpy.arange(self.nx)
        xvec=xvec-xvec.mean()        
        yvec=numpy.arange(self.ny)
        yvec=yvec-yvec.mean()
        ymat,xmat=numpy.meshgrid(yvec,xvec)
        rmat=numpy.sqrt(xmat**2+ymat**2)
        th=numpy.arctan2(xmat,ymat)
        return rmat,th
    def dot(self,map):
        tot=numpy.sum(self.map*map.map)
        return tot
def find_bad_skew_kurt(dat,skew_thresh=6.0,kurt_thresh=5.0):
    ndet=dat.shape[0]
    isgood=numpy.ones(ndet,dtype='bool')
    skew=numpy.mean(dat**3,axis=1)
    mystd=numpy.std(dat,axis=1)
    skew=skew/mystd**1.5
    mykurt=numpy.mean(dat**4,axis=1)
    kurt=mykurt/mystd**4-3
    
    isgood[numpy.abs(skew)>skew_thresh*numpy.median(numpy.abs(skew))]=False
    isgood[numpy.abs(kurt)>kurt_thresh*numpy.median(numpy.abs(kurt))]=False
    


    return skew,kurt,isgood

def timestreams_from_gauss(ra,dec,fwhm,tod,pred=None):
    if pred is None:
        pred=numpy.zeros(tod.info['dat_calib'].shape)
    n=tod.info['dat_calib'].size
    assert(pred.size==n)
    npar_src=4 #x,y,sig,amp
    dx=tod.info['dx']
    dy=tod.info['dy']
    pp=numpy.zeros(npar_src)
    pp[0]=ra
    pp[1]=dec
    pp[2]=fwhm/numpy.sqrt(8*numpy.log(2))*numpy.pi/180/3600 
    pp[3]=1
    fill_gauss_src_c(pp.ctypes.data,dx.ctypes.data,dy.ctypes.data,pred.ctypes.data,n)    
    return pred

def timestreams_from_isobeta_c(params,tod,pred=None):
    if pred is None:
        pred=numpy.zeros(tod.info['dat_calib'].shape)
    n=tod.info['dat_calib'].size
    assert(pred.size==n)
    dx=tod.info['dx']
    dy=tod.info['dy']
    fill_isobeta_c(params.ctypes.data,dx.ctypes.data,dy.ctypes.data,pred.ctypes.data,n)

    npar_beta=5 #x,y,theta,beta,amp
    npar_src=4 #x,y,sig,amp
    nsrc=(params.size-npar_beta)/npar_src
    for i in range(nsrc):
        pp=numpy.zeros(npar_src)
        ioff=i*npar_src+npar_beta
        pp[:]=params[ioff:(ioff+npar_src)]
        fill_gauss_src_c(pp.ctypes.data,dx.ctypes.data,dy.ctypes.data,pred.ctypes.data,n)


    return pred
def timestreams_from_isobeta(params,tod):
    npar_beta=5 #x,y,theta,beta,amp
    npar_src=4 #x,y,sig,amp
    nsrc=(params.size-npar_beta)/npar_src
    assert(params.size==nsrc*npar_src+npar_beta)
    x0=params[0]
    y0=params[1]
    theta=params[2]
    beta=params[3]
    amp=params[4]
    cosdec=numpy.cos(y0)


    dx=(tod.info['dx']-x0)*cosdec
    dy=tod.info['dy']-y0
    rsqr=dx*dx+dy*dy
    rsqr=rsqr/theta**2
    #print rsqr.max()
    pred=amp*(1+rsqr)**(0.5-1.5*beta)
    for i in range(nsrc):
        src_x=params[i*npar_src+npar_beta+0]
        src_y=params[i*npar_src+npar_beta+1]
        src_sig=params[i*npar_src+npar_beta+2]
        src_amp=params[i*npar_src+npar_beta+3]
        
        dx=tod.info['dx']-src_x
        dy=tod.info['dy']-src_y
        rsqr=( (dx*numpy.cos(src_y))**2+dy**2)
        pred=pred+src_amp*numpy.exp(-0.5*rsqr/src_sig**2)

    return pred

    

def isobeta_src_chisq(params,tods):
    chisq=0.0
    for tod in tods.tods:
        pred=timestreams_from_isobeta_c(params,tod)
        chisq=chisq+tod.timestream_chisq(tod.info['dat_calib']-pred)

    return chisq
    npar_beta=5 #x,y,theta,beta,amp
    npar_src=4 #x,y,sig,amp
    nsrc=(params.size-npar_beta)/npar_src
    assert(params.size==nsrc*npar_src+npar_beta)
    x0=params[0]
    y0=params[1]
    theta=params[2]
    beta=params[3]
    amp=params[4]
    cosdec=numpy.cos(y0)
    chisq=0.0
    for tod in tods.tods:
        dx=tod.info['dx']-x0
        dy=tod.info['dy']-y0
        rsqr=(dx*cosdec)**2+dy**2
        pred=amp*(1+rsqr/theta**2)**(0.5-1.5*beta)
        for i in range(nsrc):
            src_x=params[i*npar_src+npar_beta+0]
            src_y=params[i*npar_src+npar_beta+1]
            src_sig=params[i*npar_src+npar_beta+2]
            src_amp=params[i*npar_src+npar_beta+3]

            dx=tod.info['dx']-src_x
            dy=tod.info['dy']-src_y
            rsqr=( (dx*numpy.cos(src_y))**2+dy**2)
            pred=pred+src_amp*numpy.exp(-0.5*rsqr/src_sig**2)
        chisq=chisq+tod.timestream_chisq(tod.info['dat_calib']-pred)
    return chisq

class Tod:
    def __init__(self,info):
        self.info=info.copy()
        self.jumps=None
        self.cuts=None
    def lims(self):
        xmin=self.info['dx'].min()
        xmax=self.info['dx'].max()
        ymin=self.info['dy'].min()
        ymax=self.info['dy'].max()
        return xmin,xmax,ymin,ymax
    def set_tag(self,tag):
        self.info['tag']=tag
    def copy(self):
        tod=Tod(self.info)
        if not(self.jumps is None):
            try:
                tod.jumps=self.jumps.copy()
            except:
                tod.jumps=self.jumps[:]
        if not(self.cuts is None):
            try:
                tod.cuts=self.cuts.copy()
            except:
                tod.cuts=self.cuts[:]
            tod.cuts=self.cuts[:]
            
        return tod
    
    def set_noise_smoothed_svd(self,fwhm=50):
        u,s,v=numpy.linalg.svd(self.info['dat_calib'],0)
        print 'got svd'
        ndet=s.size
        n=self.info['dat_calib'].shape[1]
        self.info['v']=numpy.zeros([ndet,ndet])
        self.info['v'][:]=u.transpose()
        dat_rot=numpy.dot(self.info['v'],self.info['dat_calib'])
        dat_trans=pyfftw.fft_r2r(dat_rot)
        spec_smooth=smooth_many_vecs(dat_trans**2,fwhm)
        self.info['mywt']=1.0/spec_smooth
        #return dat_rot
        
    def apply_noise(self,dat=None):
        if dat is None:
            dat=self.info['dat_calib']
        dat_rot=numpy.dot(self.info['v'],dat)
        datft=pyfftw.fft_r2r(dat_rot)
        nn=datft.shape[1]
        datft=datft*self.info['mywt'][:,0:nn]
        dat_rot=pyfftw.fft_r2r(datft)
        dat=numpy.dot(self.info['v'].transpose(),dat_rot)
        return dat
    def dot(self,mapset,mapset_out):
        tmp=0.0*self.info['dat_calib']
        for map in mapset.maps:
            map.map2tod(self,tmp)
        tmp=self.apply_noise(tmp)
        for map in mapset_out.maps:
            map.tod2map(self,tmp)
    def set_jumps(self,jumps):
        self.jumps=jumps
    def cut_detectors(self,isgood):
        #cut all detectors not in boolean array isgood
        isbad=numpy.asarray(1-isgood,dtype='bool')
        bad_inds=numpy.where(isbad)
        bad_inds=numpy.fliplr(bad_inds)
        bad_inds=bad_inds[0]
        print bad_inds
        nkeep=numpy.sum(isgood)
        for key in self.info.keys():
            if isinstance(self.info[key],numpy.ndarray):
                self.info[key]=slice_with_copy(self.info[key],isgood)
        if not(self.jumps is None):
            for i in bad_inds:
                print 'i in bad_inds is ',i
                del(self.jumps[i])
        if not(self.cuts is None):
            for i in bad_inds:
                del(self.cuts[i])
                
    def timestream_chisq(self,dat=None):
        if dat is None:
            dat=self.info['dat_calib']
        dat_filt=self.apply_noise(dat)
        chisq=numpy.sum(dat_filt*dat)
        return chisq

def slice_with_copy(arr,ind):
    if isinstance(arr,numpy.ndarray):
        myshape=arr.shape

        if len(myshape)==1:
            ans=numpy.zeros(ind.sum(),dtype=arr.dtype)
            print ans.shape
            print ind.sum()
            ans[:]=arr[ind]
        else:   
            mydims=numpy.append(numpy.sum(ind),myshape[1:])
            print mydims,mydims.dtype
            ans=numpy.zeros(mydims,dtype=arr.dtype)
            ans[:,:]=arr[ind,:].copy()
        return ans
    return None #should not get here
class TodVec:
    def __init__(self):
        self.tods=[]
        self.ntod=0
    def add_tod(self,tod):

        self.tods.append(tod.copy())
        self.tods[-1].set_tag(self.ntod)
        self.ntod=self.ntod+1
    def lims(self):
        if self.ntod==0:
            return None
        xmin,xmax,ymin,ymax=self.tods[0].lims()
        for i in range(1,self.ntod):
            x1,x2,y1,y2=self.tods[i].lims()
            xmin=min(x1,xmin)
            xmax=max(x2,xmax)
            ymin=min(y1,ymin)
            ymax=max(y2,ymax)
        if have_mpi:
            print 'before reduction lims are ',[xmin,xmax,ymin,ymax]
            xmin=comm.allreduce(xmin,op=MPI.MIN)
            xmax=comm.allreduce(xmax,op=MPI.MAX)
            ymin=comm.allreduce(ymin,op=MPI.MIN)
            ymax=comm.allreduce(ymax,op=MPI.MAX)
            print 'after reduction lims are ',[xmin,xmax,ymin,ymax]
        return [xmin,xmax,ymin,ymax]
    def set_pix(self,map):
        for tod in self.tods:
            ipix=map.get_pix(tod)
            tod.info['ipix']=ipix
    def dot(self,mapset,mapset2=None,report_times=False):
        if mapset2 is None:
            mapset2=mapset.copy()
            mapset2.clear()

        times=numpy.zeros(self.ntod)
        #for tod in self.tods:
        for i in range(self.ntod):
            tod=self.tods[i]
            t1=time.time()
            tod.dot(mapset,mapset2)
            t2=time.time()
            times[i]=t2-t1
        if have_mpi:
            mapset2.mpi_reduce()
        if report_times:
            return mapset2,times
        else:
            return mapset2
    def make_rhs(self,mapset,do_clear=False):
        if do_clear:
            mapset.clear()
        for tod in self.tods:
            dat_filt=tod.apply_noise()
            for map in mapset.maps:
                map.tod2map(tod,dat_filt)
        
        if have_mpi:
            mapset.mpi_reduce()
def read_tod_from_fits(fname,hdu=1):
    f=pyfits.open(fname)
    raw=f[hdu].data
    pixid=raw['PIXID']
    dets=numpy.unique(pixid)
    ndet=len(dets)
    nsamp=len(pixid)/len(dets)
    print 'nsamp and ndet are ',ndet,nsamp,len(pixid),' on ',fname
    #print raw.names
    dat={}
    #this bit of odd gymnastics is because a straightforward reshape doesn't seem to leave the data in
    #memory-contiguous order, which causes problems down the road
    #also, float32 is a bit on the edge for pointing, so cast to float64
    dx=raw['DX']
    #dat['dx']=numpy.zeros([ndet,nsamp],dtype=type(dx[0]))
    dat['dx']=numpy.zeros([ndet,nsamp],dtype='float64')
    dat['dx'][:]=numpy.reshape(dx,[ndet,nsamp])[:]
    dy=raw['DY']
    #dat['dy']=numpy.zeros([ndet,nsamp],dtype=type(dy[0]))
    dat['dy']=numpy.zeros([ndet,nsamp],dtype='float64')
    dat['dy'][:]=numpy.reshape(dy,[ndet,nsamp])[:]

    tt=numpy.reshape(raw['TIME'],[ndet,nsamp])
    tt=tt[0,:]
    dt=numpy.median(numpy.diff(tt))
    dat['dt']=dt
    pixid=numpy.reshape(pixid,[ndet,nsamp])
    pixid=pixid[:,0]
    dat['pixid']=pixid
    dat_calib=raw['FNU']
    #dat['dat_calib']=numpy.zeros([ndet,nsamp],dtype=type(dat_calib[0]))
    dat['dat_calib']=numpy.zeros([ndet,nsamp],dtype='float64') #go to double because why not
    dat_calib=numpy.reshape(dat_calib,[ndet,nsamp])
    dat['dat_calib'][:]=dat_calib[:]
    dat['fname']=fname
    f.close()
    return dat


def downsample_array_r2r(arr,fac):

    n=arr.shape[1]
    nn=int(n/fac)
    arr_ft=pyfftw.fft_r2r(arr)
    arr_ft=arr_ft[:,0:nn].copy()
    arr=pyfftw.fft_r2r(arr_ft)/(2*(n-1))
    return arr

def downsample_tod(dat,fac=10):
    ndata=dat['dat_calib'].shape[1]
    keys=dat.keys()
    for key in dat.keys():
        try:
            if dat[key].shape[1]==ndata:
                #print 'downsampling ' + key
                dat[key]=downsample_array_r2r(dat[key],fac)
        except:
            #print 'not downsampling ' + key
            pass
    

def truncate_tod(dat,primes=[2,3,5,7,11]):
    
    n=dat['dat_calib'].shape[1]
    lens=find_good_fft_lens(n-1,primes)
    n_new=lens.max()+1
    if n_new<n:
        print 'truncating from ',n,' to ',n_new
        for key in dat.keys():
            try:
                if dat[key].shape[1]==n:
                    dat[key]=dat[key][:,0:n_new].copy()
            except:
                #print 'skipping key ' + key
                pass


def todvec_from_files_octave(fnames):
    todvec=TodVec()
    for fname in fnames:
        info=read_octave_struct(fname)
        tod=Tod(info)
        todvec.add_tod(tod)
    return todvec
        
def make_hits(todvec,map):
    hits=map.copy()
    hits.clear()
    for tod in todvec.tods:
        tmp=numpy.ones(tod.info['dat_calib'].shape)
        hits.tod2map(tod,tmp)
    if have_mpi:
        hits.mpi_reduce()
    return hits


def decimate(vec,nrep=1):
    for i in range(nrep):
        if len(vec)%2:
            vec=vec[:-1]
        vec=0.5*(vec[0::2]+vec[1::2])
    return vec
def plot_ps(vec,downsamp=0):
    vecft=pyfftw.fft_r2r(vec)
    
def get_wcs(lims,pixsize,proj='CAR'):
    w=wcs.WCS(naxis=2)    
    dec=0.5*(lims[2]+lims[3])
    cosdec=numpy.cos(dec)
    if proj=='CAR':
        #CAR in FITS seems to already correct for cosin(dec), which has me confused, but whatever...
        cosdec=1.0
        w.wcs.crpix=[1.0,1.0]
        w.wcs.crval=[lims[1]*180/numpy.pi,lims[2]*180/numpy.pi]
        w.wcs.cdelt=[-pixsize/cosdec*180/numpy.pi,pixsize*180/numpy.pi]
        w.wcs.ctype=['RA---CAR','DEC--CAR']
        return w
    print 'unknown projection type ',proj,' in get_wcs.'
    return None


