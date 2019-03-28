from __future__ import division, print_function, absolute_import
from CDTools.tools.cmath import *
import torch as t

__all__ = ['far_field', 'near_field', 'inverse_far_field', 'inverse_near_field', 'get_exit_waves']


def far_field(wavefront):
    """Implements a far-field propagator in torch

    This accepts a torch tensor, where the last dimension
    represents the real and imaginary components of the wavefield,
    and returns the far-field propagated version of it assuming it matches the
    detector dimensions. It assumes that the
    propagation is purely far-field, without checking that the geometry
    is consistent with that assumption.


    It also assumes that the real space wavefront is stored in an array
    [i,j] where i corresponds to the y-axis and j corresponds to the
    x-axis, with the origin following the CS standard of being in the
    upper right. The zero frequency component of the propagated wavefield is
    shifted to the center of the array.

    Args:
        wavefront (torch.Tensor) : The JxNxMx2 stack of complex wavefronts to be propagated
    Returns:
        torch.Tensor : The JxNxMx2 propagated wavefield
    """

    return fftshift(t.fft(wavefront, 2))

def inverse_far_field(wavefront):
    """Implements the inverse of the far-field propagator in torch

    This accepts a torch tensor, where the last dimension
    represents the real and imaginary components of the propagated wavefield,
    and returns the un-propagated array.

    It assumes that the real space wavefront is stored in an array
    [i,j] where i corresponds to the y-axis and j corresponds to the
    x-axis, with the origin following the CS standard of being in the
    upper right. The zero frequency component of the propagated wavefield is
    assumed to be the center of the array.

    Args:
        wavefront (torch.Tensor) : The JxNxMx2 stack of complex wavefronts propagated to the far-field
    Returns:
        torch.Tensor : The JxNxMx2 exit wavefield
    """
    return t.ifft(ifftshift(wavefront), 2)


def generate_angular_spectrum_propagator(shape, spacing, wavelength, z):
    """Generates an angular-spectrum based near-field propagator from experimental quantities

    This function generates an angular-spectrum based near field
    propagator that will work on torch Tensors. The function is structured
    this way - to generate the propagator first - because the
    generation of the propagation mask is a bit expensive and if this
    propagator is used in a reconstruction program, then it will be best
    to calculate this mask once and close over it.

    Args:
        shape (iterable) : The shape of the arrays to be propagated
        spacing (iterable) : The pixel size in each dimension of the arrays to be propagated
        wavelength (float) : The wavelength of light to simulate propagation of
        z (float) : The distance to simulate propagation over
    Returns:
        torch.Tensor : A propagation term which accounts for the phase change that each plane wave will undergo on its journey to the prediction plane.
    """

    ki = fftpack.fftfreq(shape[0],spacing[0])
    kj = fftpack.fftfreq(shape[1],spacing[1])
    Ki, Kj = np.meshgrid(ki,kj)
    propagator = np.exp(1j*np.sqrt((2*np.pi/wavelength)**2
                                - Ki**2 - Kj**2) * z)
    propagator = complex_to_float(propagator).astype(np.float32)
    propagator = t.from_numpy(propagator).cuda()

    return propagator


def near_field(wavefront, angular_spectrum_propagator):
    """This function accepts an 3d torch tensor, where the
    last dimension represents the real and imaginary components of
    the wavefield, and returns the near-field propagated version of it.


    Args:
        angular_spectrum_propagator (torch.Tensor) : The near field propagator
        wavefront (torch.Tensor) : The JxNxMx2 stack of complex wavefronts to be propagated
    Returns:
        function : The wavefront propagated to the near field
    """

    return t.ifft(angular_spectrum_propagator * t.fft(wavefront,2), 2)



def inverse_near_field(wavefront, angular_spectrum_propagator):
    """This function accepts a 3d torch tensor, where the
    last dimension represents the real and imaginary components of
    the near-field propagated wavefield, and returns the exit wavefront via an inverse transformation.


    Args:
        angular_spectrum_propagator (torch.Tensor) : The pixel size in each dimension of the arrays to be propagated
        wavefront (torch.Tensor) : The JxNxMx2 stack of complex wavefronts to be propagated
    Returns:
        function : A function to propagate a torch tensor.
    """
    return t.ifft(t.fft(wavefront,2) * angular_spectrum_propagator**-1, 2)




def get_exit_waves(probe, object, translations):
    """Returns a stack of exit waves accounting for subpixel shifts

    This function returns a collection of exit waves, with the first
    dimension as the translation index and the final dimensions
    corresponding to the detector. The exit waves are calculated by
    shifting the object with each translation in turn, using linear
    interpolation.
    Args:
        probe (torch.Tensor) : An MxM probe function for the exit waves
        object (torch.Tensor) : The object function to be probed
        translations (torch.Tensor) : The Nx2 array of translations to simulate
    Returns:
        torch.Tensor : An NxMxM tensor of the calculated exit waves
    """

    # Separate the translations into a part that chooses the window
    # And a part that defines the windowing function
    integer_translations = t.floor(translations)
    subpixel_translations = translations - integer_translations
    integer_translations = integer_translations.to(dtype=t.int32)

    selections = []
    for tr, sp in zip(integer_translations,
                      subpixel_translations):

        sel00 = object[tr[0]:tr[0]+probe.shape[0],
                    tr[1]:tr[1]+probe.shape[1]]

        sel01 = object[tr[0]:tr[0]+probe.shape[0],
                    tr[1]+1:tr[1]+1+probe.shape[1]]

        sel10 = object[tr[0]+1:tr[0]+1+probe.shape[0],
                    tr[1]:tr[1]+probe.shape[1]]

        sel11 = object[tr[0]+1:tr[0]+1+probe.shape[0],
                    tr[1]+1:tr[1]+1+probe.shape[1]]

        selections.append(sel00 * (1-sp[0])*(1-sp[1]) + \
                          sel01 * (1-sp[0])*sp[1] + \
                          sel10 * sp[0]*(1-sp[1]) + \
                          sel11 * sp[0]*sp[1])

    return t.stack([cmult(probe,selection) for selection in selections])