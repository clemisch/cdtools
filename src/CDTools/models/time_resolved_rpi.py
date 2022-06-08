import torch as t
from cdtools.models import CDIModel
from cdtools import tools
from cdtools.tools import plotting as p
from cdtools.tools.interactions import RPI_interaction
from cdtools.tools import initializers
from scipy.ndimage.morphology import binary_dilation
import numpy as np
from copy import copy

__all__ = ['MultimodeRPI']



__all__ = ['RPI']

class TimeResolvedRPI(CDIModel):

    @property
    def obj(self):
        return t.complex(self.obj_real, self.obj_imag)

    
    def __init__(self, wavelength, detector_geometry, probe_basis,
                 probe, obj_guess, framerate, detector_slice=None,
                 background=None, mask=None, saturation=None,
                 obj_support=None, oversampling=1):

        super(TimeResolvedRPI, self).__init__()

        self.wavelength = t.tensor(wavelength)
        self.framerate = framerate
        self.detector_geometry = copy(detector_geometry)
        
        det_geo = self.detector_geometry
        if hasattr(det_geo, 'distance'):
            det_geo['distance'] = t.tensor(det_geo['distance'])
        if hasattr(det_geo, 'basis'):
            det_geo['basis'] = t.tensor(det_geo['basis'])
        if hasattr(det_geo, 'corner'):
            det_geo['corner'] = t.tensor(det_geo['corner'])

        self.probe_basis = t.tensor(probe_basis)

        scale_factor = t.tensor([probe.shape[-1]/obj_guess.shape[-1],
                                 probe.shape[-2]/obj_guess.shape[-2]])
        self.obj_basis = self.probe_basis * scale_factor
        self.detector_slice = detector_slice

        # Maybe something to include in a bit
        # self.surface_normal = t.tensor(surface_normal)
        
        self.saturation = saturation
        
        if mask is None:
            self.mask = mask
        else:
            self.mask = t.tensor(mask, dtype=t.bool)

            
        self.probe = t.tensor(probe, dtype=t.complex64)

        obj_guess = t.tensor(obj_guess, dtype=t.complex64)
        
        self.obj_real = t.nn.Parameter(obj_guess.real)
        self.obj_imag = t.nn.Parameter(obj_guess.imag)
        
        # Wait for LBFGS to be updated for complex-valued parameters
        # self.obj = t.nn.Parameter(obj_guess.to(t.float32))

        if background is None:
            if detector_slice is not None:
                background = 1e-6 * t.ones(
                    self.probe[0][self.detector_slice].shape,
                    dtype=t.float32)
            else:
                background = 1e-6 * t.ones(self.probe[0].shape,
                                           dtype=t.float32)
                
        self.background = t.tensor(background, dtype=t.float32)

        if obj_support is not None:
            self.obj_support = obj_support
            self.obj.data = self.obj * obj_support[None, ...]
        else:
            self.obj_support = t.ones_like(self.obj[0, ...])

        self.oversampling = oversampling


    @classmethod
    def from_dataset(cls, dataset, probe, framerate, obj_size=None, background=None, mask=None, padding=0, saturation=None, scattering_mode=None, oversampling=1, auto_center=False, initialization='random', opt_for_fft=False, probe_threshold=0):
        
        wavelength = dataset.wavelength
        det_basis = dataset.detector_geometry['basis']
        det_shape = dataset[0][1].shape
        distance = dataset.detector_geometry['distance']

        # always do this on the cpu
        get_as_args = dataset.get_as_args
        dataset.get_as(device='cpu')
        # We only need the patterns here, not the inputs associated with them.
        _, patterns = dataset[:]
        dataset.get_as(*get_as_args[0],**get_as_args[1])

        # Set to none to avoid issues with things outside the detector
        if auto_center:
            center = tools.image_processing.centroid(t.sum(patterns,dim=0))
        else:
            center = None
            
        # Then, generate the probe geometry from the dataset
        ewg = tools.initializers.exit_wave_geometry
        probe_basis, probe_shape, det_slice =  ewg(det_basis,
                                                   det_shape,
                                                   wavelength,
                                                   distance,
                                                   center=center,
                                                   padding=padding,
                                                   opt_for_fft=opt_for_fft,
                                                   oversampling=oversampling)

        if not isinstance(probe,t.Tensor):
            probe = t.as_tensor(probe)
        
        if background is None and hasattr(dataset, 'background') \
           and dataset.background is not None:
            background = t.sqrt(dataset.background)
        elif background is not None:
            background = t.sqrt(t.Tensor(background).to(dtype=t.float32))

        det_geo = dataset.detector_geometry

        # If no mask is given, but one exists in the dataset, load it.
        if mask is None and hasattr(dataset, 'mask') \
           and dataset.mask is not None:
            mask = dataset.mask.to(t.bool)

        # Now we initialize the object
        if obj_size is None:
            # This is a standard size for a well-matched probe and detector
            obj_size = list((np.array(probe_shape) // 2).astype(int))

        # I think something to do with the fact that the object is defined
        # on a coarser grid needs to be accounted for here that is not
        # accounted for yet
        scale = t.sum(patterns[0]) / t.sum(t.abs(probe)**2)
        n_modes = (probe.shape[0] - 1) // framerate + 1

        obj_guess = scale * t.exp(2j * np.pi * t.rand([n_modes,]+obj_size))
        

        probe_intensity = t.sqrt(t.sum(t.abs(probe)**2,axis=0))
        probe_fft = tools.propagators.far_field(probe_intensity)
        pad0l = (probe.shape[-2] - obj_size[-2])//2
        pad0r = probe.shape[-2] - obj_size[-2] - pad0l
        pad1l = (probe.shape[-1] - obj_size[-1])//2
        pad1r = probe.shape[-1] - obj_size[-1] - pad1l
        probe_lr_fft = probe_fft[pad0l:-pad0r,pad1l:-pad1r]
        probe_lr = t.abs(tools.propagators.inverse_far_field(probe_lr_fft))

        obj_support = probe_lr > t.max(probe_lr) * probe_threshold
        obj_support = t.as_tensor(binary_dilation(obj_support))

        return cls(wavelength, det_geo, probe_basis,
                   probe, obj_guess, framerate, detector_slice=det_slice,
                   background=background, mask=mask, saturation=saturation,
                   obj_support=obj_support, oversampling=oversampling)


    def random_init(self, pattern):
        scale = t.sum(pattern) / t.sum(t.abs(self.probe)**2)
        self.obj.data = scale * t.exp(
            2j * np.pi * t.rand(self.obj.shape)).to(
                dtype=self.obj.dtype, device=self.obj.device)
        
    
    # Needs work
    def interaction(self, index, *args):
        # including *args allows this to work with all sorts of datasets
        # that might include other information in with the index in their
        # "input" parameters (such as translations for a ptychography dataset).
        # This makes it seamless to use such a dataset even though those
        # extra arguments will not be used.

        
        all_exit_waves = []

        # Mix the probes with the weight matrix
        prs = self.probe
        
        for i in range(self.probe.shape[0]):
            obj_frame = i // self.framerate
            pr = prs[i]
            exit_waves = RPI_interaction(pr,
                                         self.obj_support * self.obj[obj_frame])
            all_exit_waves.append(exit_waves.unsqueeze(0))

        # This creates a bunch of modes generated from all possible combos
        # of the probe and object modes all strung out along the first index

        output = t.cat(all_exit_waves)

        # If we have multiple indexes input, we unsqueeze and repeat the stack
        # of wavefields enough times to simulate each requested index. This
        # seems silly, but it enables (for example) one to do a reconstruction
        # from a set of diffraction patterns that are all known to be from the
        # same object.
        try:
            # will fail if index has no length, for example when index
            # is just an int. In this case, we just do nothing instead
            output = output.unsqueeze(0).repeat(1,len(index),1,1,1)
        except TypeError:
            pass
        return output


    def forward_propagator(self, wavefields):
        return tools.propagators.far_field(wavefields)


    def backward_propagator(self, wavefields):
        return tools.propagators.inverse_far_field(wavefields)

    
    def measurement(self, wavefields):
        # Here I'm taking advantage of an undocumented feature in the
        # incoherent_sum measurement function where it will work with
        # a 4D wavefield array as well as a 5D array.
        return tools.measurements.quadratic_background(wavefields,
                            self.background,
                            detector_slice=self.detector_slice,
                            measurement=tools.measurements.incoherent_sum,
                            saturation=self.saturation,
                            oversampling=self.oversampling)
    
    def loss(self, sim_data, real_data, mask=None):
        return tools.losses.amplitude_mse(real_data, sim_data, mask=mask)
        #return tools.losses.poisson_nll(real_data, sim_data, mask=mask)

    def regularizer(self, factors):
        return factors[0] * t.sum(t.abs(self.obj[0,:,:])**2) \
            + factors[1] * t.sum(t.abs(self.obj[1:,:,:])**2)
        
    def to(self, *args, **kwargs):
        super(TimeResolvedRPI, self).to(*args, **kwargs)
        self.wavelength = self.wavelength.to(*args,**kwargs)
        # move the detector geometry too
        det_geo = self.detector_geometry
        if hasattr(det_geo, 'distance'):
            det_geo['distance'] = det_geo['distance'].to(*args,**kwargs)
        if hasattr(det_geo, 'basis'):
            det_geo['basis'] = det_geo['basis'].to(*args,**kwargs)
        if hasattr(det_geo, 'corner'):
            det_geo['corner'] = det_geo['corner'].to(*args,**kwargs)

        if self.mask is not None:
            self.mask = self.mask.to(*args, **kwargs)

        self.probe = self.probe.to(*args,**kwargs)
        self.probe_basis = self.probe_basis.to(*args,**kwargs)
        self.obj_basis = self.obj_basis.to(*args,**kwargs)
        self.obj_support = self.obj_support.to(*args,**kwargs)
        self.background = self.background.to(*args, **kwargs)
        
        # Maybe include in a bit
        #self.surface_normal = self.surface_normal.to(*args, **kwargs)

    def sim_to_dataset(self, args_list):
        raise NotImplementedError('No sim to dataset yet, sorry!')

    plot_list = [
        ('Probe Amplitudes',
         lambda self, fig: p.plot_amplitude(self.probe, fig=fig, basis=self.probe_basis)),
        ('Object Amplitudes', 
         lambda self, fig: p.plot_amplitude(self.obj, fig=fig,
                                            basis=self.obj_basis)),
        ('Object Phases',
         lambda self, fig: p.plot_phase(self.obj, fig=fig,
                                        basis=self.obj_basis))
    ]


    def save_results(self, dataset=None, full_obj=False):
        # dataset is set as a kwarg here because it isn't needed, but the
        # common pattern is to pass a dataset. This makes it okay if one
        # continues to use that standard pattern
        probe_basis = self.probe_basis.detach().cpu().numpy()
        obj_basis = self.obj_basis.detach().cpu().numpy()
        probe = self.probe.detach().cpu().numpy()
        # Provide the option to save out the subdominant objects or
        # just the dominant one
        if full_obj:
            obj = self.obj.detach().cpu().numpy()
        else:
            obj = self.obj[0].detach().cpu().numpy()
        background = self.background.detach().cpu().numpy()**2
        
        return {'probe_basis': probe_basis, 'obj_basis': obj_basis,
                'probe': probe,'obj': obj,
                'background': background}

