"""Contains basic functions for analyzing the results of reconstructions

The functions in this module are designed to work either with pytorch tensors
or numpy arrays, so they can be used either directly after reconstructions
on the attributes of the models themselves, or after-the-fact once the
data has been stored in numpy arrays.
"""

import torch as t
import numpy as np
from cdtools.tools import image_processing as ip
from scipy import fftpack
from scipy import linalg as sla
from scipy import special

__all__ = ['orthogonalize_probes', 'standardize', 'synthesize_reconstructions',
           'calc_consistency_prtf', 'calc_deconvolved_cross_correlation',
           'calc_frc', 'calc_vn_entropy', 'calc_top_mode_fraction',
           'calc_rms_error', 'calc_fidelity', 'calc_generalized_rms_error']


def orthogonalize_probes(probes, density_matrix=None, keep_transform=False, normalize=False):
    """Orthogonalizes a set of incoherently mixing probes

    The strategy is to define a reduced orthogonal basis that spans
    all of the retrieved probes, and then build the density matrix
    defined by the probes in that basis. After diagonalization, the
    eigenvectors can be recast into the original basis and returned

    By default, it assumes that the set of probes are defined just as
    standard incoherently mixing probe modes, and orthogonalizes them.
    However, if a density matrix is explicitly given, it will instead
    consider the problem of extracting the eigenbasis of the matrix
    probes * denstity_matrix * probes^dagger, where probes is the
    column matrix of the given probe functions. This latter problem arises
    in the generalization of the probe mixing model, and reduces to the
    simpler case when the density matrix is equal to the identity matrix
    
    If the parameter "keep_transform" is set, the function will additionally
    return the matrix A such that A * ortho_probes^dagger = probes^dagger

    If the parameter "normalize" is False (as is the default), the variation
    in intensities in the probe modes will be kept in the probe modes, as is
    natural for a purely incoherent model. If it is set to "True", the
    returned probe modes will all be normalized instead.

    Parameters
    ----------
    probes : array
        An l x n x m complex array representing  a stack of probes
    density_matrix : np.array
        An optional l x l density matrix further elaborating on the state
    keep_transform : bool
        Default False, whether to return the map from probes to ortho_probes
    normalize : bool
        Default False, whether to normalize the probe modes

    Returns
    -------
    ortho_probes: array
        An l x n x m complex array representing a stack of probes
    """
    
    try:
        probes = probes.detach().cpu().numpy()
        send_to_torch = True
    except:
        send_to_torch = False

    # We can do the orthogonalization with an SVD, so first we have to
    # reshape the final two dimensions (the image shape) into a single
    # vectorized dimension. This matrix is probes^dagger, hence the
    # conjugation
    probes_mat = probes.reshape(probes.shape[0],
                                probes.shape[1]*probes.shape[2])

    if density_matrix is None:
        density_matrix = np.eye(probes.shape[0])
    
        
    # next we want to extract the eigendecomposition of the density matrix
    # itself
    w,v = sla.eigh(density_matrix)
    w = w[::-1]
    v = v[:,::-1]

    # We do this just to avoid total failure when the density
    # matrix is not positive definite.
    # In most cases (such as when rho is generated directly from some other
    # matrix A such that rho=A A^dagger), w should never have any negative
    # entries.
    w = np.maximum(w,0)

    B_dagger = np.dot(np.diag(np.sqrt(w)), v.conj().transpose())

    #u,s,vh = np.linalg.svd(np.dot(B_dagger,probes_mat), full_matrices=False)
    u,s,vh = sla.svd(np.dot(B_dagger,probes_mat), full_matrices=False)
    

    if normalize:
        ortho_probes = vh.reshape(probes.shape[0],
                                  probes.shape[1],
                                  probes.shape[2])

        B_dagger_inv = np.linalg.pinv(B_dagger)
        A = np.dot(B_dagger_inv,np.dot(u,np.diag(s)))
        #A_dagger = np.dot(np.linalg.pinv(np.diag(s)),
        #                  np.dot(np.transpose(u).conj(),B_dagger))
    else:
        ortho_probes = np.dot(np.diag(s),vh).reshape(probes.shape[0],
                                                     probes.shape[1],
                                                     probes.shape[2])
        B_dagger_inv = np.linalg.pinv(B_dagger)
        A = np.dot(B_dagger_inv,u)
        #A_dagger = np.dot(np.transpose(u).conj(),B_dagger)
    
    if send_to_torch:
        ortho_probes = t.as_tensor(np.stack(ortho_probes))
        A = t.as_tensor(A)

    if keep_transform:
        return ortho_probes, A#_dagger
    else:
        return ortho_probes 

    
def standardize(probe, obj, obj_slice=None, correct_ramp=False):
    """Standardizes a probe and object to prepare them for comparison

    There are a number of ambiguities in the definition of a ptychographic
    reconstruction. This function makes an explicit choice for each ambiguity
    to allow comparisons between independent reconstructions without confusing
    these ambiguities for real differences between the reconstructions.

    The ambiguities and standardizations are:

    1) a. Probe and object can be scaled inversely to one another
       b. So we set the probe intensity to an average per-pixel value of 1
    2) a. The probe and object can aquire equal and opposite phase ramps
       b. So we set the centroid of the FFT of the probe to zero frequency
    3) a. The probe and object can each acquire an arbitrary overall phase
       b. So we set the phase of the sum of all values of both the probe and object to 0

    When dealing with the properties of the object, a slice is used by
    default as the edges of the object often are dominated by unphysical
    noise. The default slice is from 3/8 to 5/8 of the way across. If the
    probe is actually a stack of incoherently mixing probes, then the
    dominant probe mode (assumed to be the first in the list) is used, but
    all the probes are updated with the same factors.

    Parameters
    ----------
    probe : array
        A complex array storing a retrieved probe or stack of incoherently mixed probes
    obj : array
        A complex array storing a retrieved object
    obj_slice : slice
        Optional, a slice to take from the object for calculating normalizations
    correct_ramp : bool
        Default False, whether to correct for the relative phase ramps

    Returns
    -------
    standardized_probe : array
        The standardized probe
    standardized_obj : array
        The standardized object

    """
    # First, we normalize the probe intensity to a fixed value.
    probe_np = False
    if isinstance(probe, np.ndarray):
        probe = t.as_tensor(probe, dtype=t.complex64)
        probe_np = True
    obj_np = False
    if isinstance(obj, np.ndarray):
        obj = t.as_tensor(obj,dtype=t.complex64)
        obj_np = True

    # If this is a single probe and not a stack of probes
    if len(probe.shape) == 2:
        probe = probe[None,...]
        single_probe = True
    else:
        single_probe = False

    normalization = t.sqrt(t.mean(t.abs(probe[0])**2))
    probe = probe / normalization
    obj = obj * normalization

    # Default slice of the object to use for alignment, etc.
    if obj_slice is None:
        obj_slice = np.s_[(obj.shape[0]//8)*3:(obj.shape[0]//8)*5,
                          (obj.shape[1]//8)*3:(obj.shape[1]//8)*5]


    if correct_ramp:
        # Need to check if this is actually working and, if not, why not
        center_freq = ip.centroid(t.abs(t.fft.fftshift(t.fft.fft2(probe[0]),
                                                       dim=(-1,-2)))**2)
        center_freq -= t.div(t.tensor(probe[0].shape,dtype=t.float32),2,rounding_mode='floor')
        center_freq /= t.as_tensor(probe[0].shape,dtype=t.float32)

        Is, Js = np.mgrid[:probe[0].shape[0],:probe[0].shape[1]]
        probe_phase_ramp = t.exp(2j * np.pi *
                                 (center_freq[0] * t.tensor(Is).to(t.float32) +
                                  center_freq[1] * t.tensor(Js).to(t.float32)))
        probe = probe *  t.conj(probe_phase_ramp)
        Is, Js = np.mgrid[:obj.shape[0],:obj.shape[1]]
        obj_phase_ramp = t.exp(2j*np.pi *
                               (center_freq[0] * t.tensor(Is).to(t.float32) +
                                center_freq[1] * t.tensor(Js).to(t.float32)))
        obj = obj * obj_phase_ramp

    # Then, we set them to consistent absolute phases

    obj_angle = t.angle(t.sum(obj[obj_slice]))
    obj = obj *  t.exp(-1j*obj_angle)

    for i in range(probe.shape[0]):
        probe_angle = t.angle(t.sum(probe[i]))
        probe[i] = probe[i] * t.exp(-1j*probe_angle)

    if single_probe:
        probe = probe[0]

    if probe_np:
        probe = probe.detach().cpu().numpy()
    if obj_np:
        obj = obj.detach().cpu().numpy()

    return probe, obj



def synthesize_reconstructions(probes, objects, use_probe=False, obj_slice=None, correct_ramp=False):
    """Takes a collection of reconstructions and outputs a single synthesized probe and object

    The function first standardizes the sets of probes and objects using the
    standardize function, passing through the relevant options. Then it
    calculates the closest overlap of subsequent frames to subpixel
    precision and uses a sinc interpolation to shift all the probes and objects
    to a common frame. Then the images are summed.

    Parameters
    ----------
    probes : list(array)
        A list of probes or stacks of probe modes
    objects : list(array)
        A list of objects
    use_probe : bool
        Default False, whether to use the probe or object for alignment
    obj_slice : slice
        Optional, A slice of the object to use for alignment and normalization
    correct_ramp : bool
        Default False, whether to correct for a relative phase ramp in the probe and object

    Returns
    -------
    synth_probe : array
        The synthesized probe
    synth_obj : array
        The synthesized object
    obj_stack : list(array)
        A list of standardized objects, for further processing
    """

    # This should be cleaned up so it accepts anything array_like
    probe_np = False
    if isinstance(probes[0], np.ndarray):
        probes = [t.as_tensor(probe,dtype=t.complex64) for probe in probes]
        probe_np = True
    obj_np = False
    if isinstance(objects[0], np.ndarray):
        objects = [t.as_tensor(obj,dtype=t.complex64) for obj in objects]
        obj_np = True

    obj_shape = np.min(np.array([obj.shape for obj in objects]),axis=0)
    objects = [obj[:obj_shape[0],:obj_shape[1]] for obj in objects]

    if obj_slice is None:
        obj_slice = np.s_[(objects[0].shape[0]//8)*3:(objects[0].shape[0]//8)*5,
                          (objects[0].shape[1]//8)*3:(objects[0].shape[1]//8)*5]



    synth_probe, synth_obj = standardize(probes[0].clone(), objects[0].clone(), obj_slice=obj_slice,correct_ramp=correct_ramp)

    obj_stack = [synth_obj]

    for i, (probe, obj) in enumerate(zip(probes[1:],objects[1:])):
        probe, obj = standardize(probe.clone(), obj.clone(), obj_slice=obj_slice,correct_ramp=correct_ramp)
        if use_probe:
            shift = ip.find_shift(synth_probe[0],probe[0], resolution=50)
        else:
            shift = ip.find_shift(synth_obj[obj_slice],obj[obj_slice], resolution=50)


        obj = ip.sinc_subpixel_shift(obj,np.array(shift))

        if len(probe.shape) == 3:
            probe = t.stack([ip.sinc_subpixel_shift(p,tuple(shift))
                             for p in probe],dim=0)
        else:
            probe = ip.sinc_subpixel_shift(probe,tuple(shift))

        synth_probe = synth_probe + probe
        synth_obj = synth_obj + obj
        obj_stack.append(obj)


    # If there only was one image
    try:
        i
    except:
        i = -1

    if probe_np:
        synth_probe = synth_probe.numpy()
    if obj_np:
        synth_obj = synth_obj.numpy()
        obj_stack = [obj.numpy() for obj in obj_stack]

    return synth_probe/(i+2), synth_obj/(i+2), obj_stack



def calc_consistency_prtf(synth_obj, objects, basis, obj_slice=None,nbins=None):
    """Calculates a PRTF between each the individual objects and a synthesized one

    The consistency PRTF at any given spatial frequency is defined as the ratio
    between the intensity of any given reconstruction and the intensity
    of a synthesized or averaged reconstruction at that spatial frequency.
    Typically, the PRTF is averaged over spatial frequencies with the same
    magnitude.

    Parameters
    ----------
    synth_obj : array
        The synthesized object in the numerator of the PRTF
    objects : list(array)
        A list of objects or diffraction patterns for the denomenator of the PRTF
    basis : array
        The basis for the reconstruction array to allow output in physical unit
    obj_slice : slice
        Optional, a slice of the objects to use for calculating the PRTF
    nbins : int
        Optional, number of bins to use in the histogram. Defaults to a sensible value

    Returns
    -------
    freqs : array
        The frequencies for the PRTF
    PRTF : array
        The values of the PRTF
    """

    obj_np = False
    if isinstance(objects[0], np.ndarray):
        objects = [t.as_tensor(obj, dtype=t.complex64) for obj in objects]
        obj_np = True
    if isinstance(synth_obj, np.ndarray):
        synth_obj = t.as_tensor(synth_obj, dtype=t.complex64)

    if isinstance(basis, t.Tensor):
        basis = basis.detach().cpu().numpy()

    if obj_slice is None:
        obj_slice = np.s_[(objects[0].shape[0]//8)*3:(objects[0].shape[0]//8)*5,
                          (objects[0].shape[1]//8)*3:(objects[0].shape[1]//8)*5]

    if nbins is None:
        nbins = np.max(synth_obj[obj_slice].shape) // 4

    synth_fft = (t.abs(t.fft.fftshift(t.fft.fft2(synth_obj[obj_slice]), dim=(-1,-2)))**2).numpy()


    di = np.linalg.norm(basis[:,0])
    dj = np.linalg.norm(basis[:,1])

    i_freqs = fftpack.fftshift(fftpack.fftfreq(synth_fft.shape[0],d=di))
    j_freqs = fftpack.fftshift(fftpack.fftfreq(synth_fft.shape[1],d=dj))

    Js,Is = np.meshgrid(j_freqs,i_freqs)
    Rs = np.sqrt(Is**2+Js**2)


    synth_ints, bins = np.histogram(Rs,bins=nbins,weights=synth_fft)

    prtfs = []
    for obj in objects:
        obj = obj[obj_slice]
        single_fft = (t.abs(t.fft.fftshift(t.fft.fft2(obj),
                                           dim=(-1,-2)))**2).numpy()
        single_ints, bins = np.histogram(Rs,bins=nbins,weights=single_fft)

        prtfs.append(synth_ints/single_ints)


    prtf = np.mean(prtfs,axis=0)

    if not obj_np:
        bins = t.Tensor(bins)
        prtf = t.Tensor(prtf)

    return bins[:-1], prtf



def calc_deconvolved_cross_correlation(im1, im2, im_slice=None):
    """Calculates a cross-correlation between two images with their autocorrelations deconvolved.

    This is formally defined as the inverse Fourier transform of the normalized
    product of the Fourier transforms of the two images. It results in a
    kernel, whose characteristic size is related to the exactness of the
    possible alignment between the two images, on top of a random background

    Parameters
    ----------
    im1 : array
        The first image, as a complex or real valued array
    im2 : array
        The first image, as a complex or real valued array
    im_slice : slice
        Default is from 3/8 to 5/8 across the image, a slice to use in the processing.

    Returns
    -------
    corr : array
        The complex-valued deconvolved cross-correlation, in real space

    """

    im_np = False
    if isinstance(im1, np.ndarray):
        im1 = t.as_tensor(im1)
        im_np = True
    if isinstance(im2, np.ndarray):
        im2 = t.as_tensor(im2)
        im_np = True

    if im_slice is None:
        im_slice = np.s_[(im1.shape[0]//8)*3:(im1.shape[0]//8)*5,
                          (im1.shape[1]//8)*3:(im1.shape[1]//8)*5]


    cor_fft = t.fft.fft2(im1[im_slice]) * \
        t.conj(t.fft.fft2(im2[im_slice]))

    # Not sure if this is more or less stable than just the correlation
    # maximum - requires some testing
    cor = t.fft.ifft2(cor_fft / t.abs(cor_fft))

    if im_np:
        cor = cor.numpy()

    return cor


def calc_frc(im1, im2, basis, im_slice=None, nbins=None, snr=1., limit='side'):
    """Calculates a Fourier ring correlation between two images

    This function requires an input of a basis to allow for FRC calculations
    to be related to physical units.

    Like other analysis functions, this can take input in numpy or pytorch,
    and will return output in the respective format.

    Parameters
    ----------
    im1 : array
        The first image, a complex or real valued array
    im2 : array
        The first image, a complex or real valued array
    basis : array
        The basis for the images, defined as is standard for datasets
    im_slice : slice
        Default is from 3/8 to 5/8 across the image, a slice to use in the processing.
    nbins : int
        Number of bins to break the FRC up into
    snr : float
        The signal to noise ratio (for the combined information in both images) to return a threshold curve for.
    limit : str
        Default is 'side'. What is the highest frequency to calculate the FRC to? If 'side', it chooses the side of the Fourier transform, if 'corner' it goes fully to the corner.

    Returns
    -------
    freqs : array
        The frequencies associated with each FRC value
    FRC : array
        The FRC values
    threshold : array
        The threshold curve for comparison

    """

    im_np = False
    if isinstance(im1, np.ndarray):
        im1 = t.as_tensor(im1)
        im_np = True
    if isinstance(im2, np.ndarray):
        im2 = t.as_tensor(im2)
        im_np = True

    if isinstance(basis, np.ndarray):
        basis = t.tensor(basis)

    if im_slice is None:
        im_slice = np.s_[(im1.shape[0]//8)*3:(im1.shape[0]//8)*5,
                          (im1.shape[1]//8)*3:(im1.shape[1]//8)*5]

    if nbins is None:
        nbins = np.max(im1[im_slice].shape) // 8

    f1 = t.fft.fftshift(t.fft.fft2(im1[im_slice]),dim=(-1,-2))
    f2 = t.fft.fftshift(t.fft.fft2(im2[im_slice]),dim=(-1,-2))
    cor_fft = f1 * t.conj(f2)

    F1 = t.abs(f1)**2
    F2 = t.abs(f2)**2


    di = np.linalg.norm(basis[:,0])
    dj = np.linalg.norm(basis[:,1])

    i_freqs = fftpack.fftshift(fftpack.fftfreq(cor_fft.shape[0],d=di))
    j_freqs = fftpack.fftshift(fftpack.fftfreq(cor_fft.shape[1],d=dj))

    Js,Is = np.meshgrid(j_freqs,i_freqs)
    Rs = np.sqrt(Is**2+Js**2)

    if limit.lower().strip() == 'side':
        max_i = np.max(i_freqs)
        max_j = np.max(j_freqs)
        frc_range = [0, max(max_i,max_j)]
    elif limit.lower().strip() == 'corner':
        frc_range = [0, np.max(Rs)]
    else:
        raise ValueError('Invalid FRC limit: choose "side" or "corner"')

    numerator, bins = np.histogram(Rs, bins=nbins, range=frc_range,
                                   weights=cor_fft.numpy())
    denominator_F1, bins = np.histogram(Rs, bins=nbins, range=frc_range,
                                        weights=F1.detach().cpu().numpy())
    denominator_F2, bins = np.histogram(Rs, bins=nbins, range=frc_range,
                                        weights=F2.detach().cpu().numpy())
    n_pix, bins = np.histogram(Rs, bins=nbins, range=frc_range)

    
    #n_pix = n_pix / 4 # This is for an apodized image, apodized with a hann window
    
    frc = np.abs(numerator) / np.sqrt(denominator_F1*denominator_F2)

    # This moves from combined-image SNR to single-image SNR
    snr /= 2

    # NOTE: I should update this  to produce lots of different threshold curves
    # sigma, 2sigma, 3sigma, traditional FRC 1-bit, my better one, n_pix, etc.
    
    threshold = (snr + (2 * np.sqrt(snr) + 1) / np.sqrt(n_pix)) / \
        (1 + snr + (2 * np.sqrt(snr)) / np.sqrt(n_pix))

    my_threshold = np.sqrt(snr**2 + (2*snr**2 + 2*snr + 1)/n_pix) / \
        np.sqrt(snr**2 + 2 * snr + 1 + 2*snr**2 / n_pix)

    twosigma_threshold = 2/ np.sqrt(n_pix)
    
    if not im_np:
        bins = t.tensor(bins)
        frc = t.tensor(frc)
        threshold = t.tensor(threshold)

    return bins[:-1], frc, threshold


def calc_vn_entropy(matrix):
    """Calculates the Von Neumann entropy of a density matrix
    
    Will either accept a single matrix, or a stack of matrices. Matrices
    are assumed to be Hermetian and positive definite, to be well-formed
    density matrices
    
    Parameters
    ----------
    matrix : np.array
        The nxn matrix or lxnxn stack of matrices to calculate the entropy of

    
    Returns
    -------
    entropy: float or np.array
        The entropy or entropies of the arrays
    """

    if len(matrix.shape) == 3:
        # Get the eigenvalues
        eigs = [np.linalg.eigh(mat)[0] for mat in matrix]
        # Normalize them to match standard density matrix form
        eigs = [eig / np.sum(eig) for eig in eigs]
        # And calculate the VN entropy!
        entropies = [-np.sum(special.xlogy(eig,eig)) for eig in eigs]
        return np.array(entropies)
    else:
        eig = np.linalg.eigh(matrix)[0]
        entropy = -np.sum(special.xlogy(eig,eig))/np.sum(eig)
        return entropy

    
def calc_top_mode_fraction(matrix):
    """Calculates the fraction of total power in the top mode of a density matrix
    
    Will either accept a single matrix, or a stack of matrices. Matrices
    are assumed to be Hermetian and positive definite, to be well-formed
    density matrices
    
    Parameters
    ----------
    matrix : np.array
        The nxn matrix or lxnxn stack of matrices to work from

    
    Returns
    -------
    entropy: float or np.array
        The fraction of power in the top mode of each matrix
    """

    if len(matrix.shape) == 3:
        # Get the eigenvalues
        eigs = [np.linalg.eigh(mat)[0] for mat in matrix]
        # Normalize them to match standard density matrix form
        fractions = [np.max(eig) / np.sum(eig) for eig in eigs]
        return np.array(fractions)
    else:
        eig = np.linalg.eigh(matrix)[0]
        fraction = np.max(eig) / np.sum(eig)
        return fraction
    

def calc_rms_error(field_1, field_2, align_phases=True, normalize=False,
                   dims=2):
    """Calculates the root-mean-squared error between two complex wavefields

    The formal definition of this function is:

    output = norm * sqrt(mean(abs(field_1 - gamma * field_2)**2))
    
    Where norm is an optional normalization factor, and gamma is an
    optional phase factor which is appropriate when the wavefields suffer
    from a global phase degeneracy as is often the case in diffractive
    imaging.

    The normalization is defined as the square root of the total intensity
    contained in field_1, which is appropriate when field_1 represents a
    known ground truth:

    norm = sqrt(mean(abs(field_1)**2))
    
    The phase offset is an analytic expression for the phase offset which
    will minimize the RMS error between the two wavefields:

    gamma = exp(1j * angle(sum(field_1 * conj(field_2))))
    
    This implementation is stable even in cases where field_1 and field_2
    are completely orthogonal.

    In the definitions above, the field_n are n-dimensional wavefields. The
    dimensionality of the wavefields can be altered via the dims argument,
    but the default is 2 for a 2D wavefield.
    
    Parameters
    ----------
    field_1 : array
        The first complex-valued field
    field_2 : array
        The second complex-valued field
    align_phases : bool
        Default is True, whether to account for a global phase offset
    normalize : bool
        Default is False, whether to normalize to the intensity of field_1
    dims : (int or tuple of python:ints)
        Default is 2, the number of final dimensions to reduce over.

    
    Returns
    -------
    rms_error : float or t.Tensor
        The RMS error, or tensor of RMS errors, depending on the dim argument

    """

    sumdims = tuple(d - dims for d in range(dims))
        
    if align_phases:
        # Keepdim allows us to broadcast the result correctly when we
        # multiply by the fields
        gamma = t.exp(1j * t.angle(t.sum(field_1 * t.conj(field_2), dim=sumdims,
                                         keepdim=True)))
    else:
        gamma = 1

    if normalize:
        norm = 1 / t.mean(t.abs(field_1)**2, dim=sumdims)
    else:
        norm = 1

    difference = field_1 - gamma * field_2
    
    return t.sqrt(norm * t.mean(t.abs(difference)**2, dim=sumdims))


def calc_fidelity(fields_1, fields_2, dims=2):
    """Calculates the fidelity between two density matrices

    The fidelity is a comparison metric between two density matrices
    (i.e. mutual coherence functions) that extends the idea of the
    overlap to incoherent light. As a reminder, the overlap between two
    fields is:

    overlap = abs(sum(field_1 * field_2))**2
    
    Whereas the fidelity is defined as:
    
    fidelity = trace(sqrt(sqrt(dm_1) <dot> dm_2 <dot> sqrt(dm_1)))**2

    where dm_n refers to the density matrix encoded by fields_n such
    that dm_n = fields_n <dot> fields_<n>.conjtranspose(), sqrt
    refers to the matrix square root, and <dot> is the matrix product.
    
    This is not a practical implementation, however, as it is not feasible
    to explicitly construct the matrices dm_1 and dm_2 in memory. Therefore,
    we take advantage of the alternate definition based directly on the
    fields_<n> parameter:

    fidelity = sum(svdvals(fields_1 <dot> fields_2.conjtranspose()))**2
    
    In the definitions above, the fields_n are regarded as collections of
    wavefields, where each wavefield is by default 2-dimensional. The
    dimensionality of the wavefields can be altered via the dims argument,
    but the fields_n arguments must always have at least one more dimension
    than the dims argument. Any additional dimensions are treated as batch
    dimensions.
    
    Parameters
    ----------
    fields_1 : array
        The first set of complex-valued field modes
    fields_2 : array
        The second set of complex-valued field modes
    dims : int
        Default is 2, the number of final dimensions to reduce over.

    
    Returns
    -------
    fidelity : float or t.Tensor
        The fidelity, or tensor of fidelities, depending on the dim argument

    """

    fields_1 = t.as_tensor(fields_1)
    fields_2 = t.as_tensor(fields_2)
    
    mult = fields_1.unsqueeze(-dims-2) * fields_2.unsqueeze(-dims-1).conj()
    sumdims = tuple(d - dims for d in range(dims))
    mat = t.sum(mult,dim=sumdims)

    # Because I think this is the nuclear norm squared, I would like to swap
    # Out the definition for this, but I need to test it before swapping.
    # It also probably makes sense to implement sqrt_fidelity separately
    # because that's more important
    #return t.linalg.matrix_norm(mat, ord='nuc')**2
    
    # I think this is just the nuclear norm.
    svdvals = t.linalg.svdvals(mat)
    return t.sum(svdvals, dim=-1)**2


def calc_generalized_rms_error(fields_1, fields_2, normalize=False, dims=2):
    """Calculates a generalization of the root-mean-squared error between two complex wavefields

    This function calculates an generalization of the RMS error which uses the
    concept of fidelity to extend it to capture the error between
    incoherent wavefields, defined as a mode decomposition. The extension has
    several nice properties, in particular:

    1) For coherent wavefields, it precisely matches the RMS error including
       a correction for the global phase degeneracy (align_phases=True)
    2) All mode decompositions of either field that correspond to the same
       density matrix / mutual coherence function will produce the same 
       output
    3) The error will only be zero when comparing mode decompositions that
       correspond to the same density matrix.
    4) Due to (2), one need not worry about the ordering of the modes,
       properly orthogonalizing the modes, and it is even possible to
       compare mode decompositions with different numbers of modes.    
    
    The formal definition of this function is:

    output = norm * sqrt(mean(abs(fields_1)**2)
                         + mean(abs(fields_2)**2)
                         - 2 * sqrt(fidelity(fields_1,fields_2)))
    
    Where norm is an optional normalization factor, and the fidelity is
    defined based on the mean, rather than the sum, to match the convention
    for the root *mean* squared error.

    The normalization is defined as the square root of the total intensity
    contained in fields_1, which is appropriate when fields_1 represents a
    known ground truth:

    norm = sqrt(mean(abs(fields_1)**2))

    In the definitions above, the fields_n are regarded as collections of
    wavefields, where each wavefield is by default 2-dimensional. The
    dimensionality of the wavefields can be altered via the dims argument,
    but the fields_n arguments must always have at least one more dimension
    than the dims argument. Any additional dimensions are treated as batch
    dimensions.
    
    Parameters
    ----------
    fields_1 : array
        The first set of complex-valued field modes
    fields_2 : array
        The second set of complex-valued field modes
    normalize : bool
        Default is False, whether to normalize to the intensity of fields_1
    dims : (int or tuple of python:ints)
        Default is 2, the number of final dimensions to reduce over.

    Returns
    -------
    rms_error : float or t.Tensor
        The generalized RMS error, or tensor of generalized RMS errors, depending on the dim argument

    """
    fields_1 = t.as_tensor(fields_1)
    fields_2 = t.as_tensor(fields_2)

    npix = t.prod(t.as_tensor(fields_1.shape[-dims:],dtype=t.int32))
    
    sumdims = tuple(d - dims - 1 for d in range(dims+1))
    fields_1_intensity = t.sum(t.abs(fields_1)**2,dim=sumdims) / npix
    fields_2_intensity = t.sum(t.abs(fields_2)**2,dim=sumdims) / npix
    fidelity = calc_fidelity(fields_1, fields_2, dims=dims) / npix**2

    result = fields_1_intensity + fields_2_intensity - 2 * t.sqrt(fidelity)
    
    if normalize:
        result /= fields_1_intensity

    return t.sqrt(result)
    

def calc_generalized_frc(fields_1, fields_2, basis, im_slice=None, nbins=None, snr=1.):
    """Calculates a Fourier ring correlation between two images

    This function requires an input of a basis to allow for FRC calculations
    to be related to physical units.

    Like other analysis functions, this can take input in numpy or pytorch,
    and will return output in the respective format.

    Parameters
    ----------
    im1 : array
        The first image, a complex or real valued array
    im2 : array
        The first image, a complex or real valued array
    basis : array
        The basis for the images, defined as is standard for datasets
    im_slice : slice
        Default is from 3/8 to 5/8 across the image, a slice to use in the processing.
    nbins : int
        Number of bins to break the FRC up into
    snr : float
        The signal to noise ratio (for the combined information in both images) to return a threshold curve for.

    Returns
    -------
    freqs : array
        The frequencies associated with each FRC value
    FRC : array
        The FRC values
    threshold : array
        The threshold curve for comparison

    """

    im_np = False
    if isinstance(fields_1, np.ndarray):
        fields_1 = t.as_tensor(fields_)
        im_np = True
    if isinstance(fields_2, np.ndarray):
        fields_2 = t.as_tensor(fields_2)
        im_np = True

    if isinstance(basis, np.ndarray):
        basis = t.tensor(basis)

    if im_slice is None:
        im_slice = np.s_[(im1.shape[0]//8)*3:(im1.shape[0]//8)*5,
                          (im1.shape[1]//8)*3:(im1.shape[1]//8)*5]

    if nbins is None:
        nbins = np.max(fields_1[...,im_slice].shape[-2:]) // 4


    f1 = t.fft.fftshift(t.fft.fft2(im1[im_slice]),dim=(-1,-2))
    f2 = t.fft.fftshift(t.fft.fft2(im2[im_slice]),dim=(-1,-2))
    cor_fft = f1 * t.conj(f2)
        

    F1 = t.abs(f1)**2
    F2 = t.abs(f2)**2


    di = np.linalg.norm(basis[:,0])
    dj = np.linalg.norm(basis[:,1])

    i_freqs = fftpack.fftshift(fftpack.fftfreq(cor_fft.shape[0],d=di))
    j_freqs = fftpack.fftshift(fftpack.fftfreq(cor_fft.shape[1],d=dj))

    Js,Is = np.meshgrid(j_freqs,i_freqs)
    Rs = np.sqrt(Is**2+Js**2)

    # This line is used to get a set of bins that matches the logic
    # used by np.histogram, so that this function will match the choices
    # of bin edges that comes from the non-generalized version. This also
    # gets us the count on the number of pixels per bin so we can calculate
    # the threshold curve
    n_pix, bins = np.histogram(Rs,bins=nbins)

    frc = []
    for i in range(len(bins)-1):
        mask = t.logical_and(Rs<bins[i+1], Rs>=bins[i])
        masked_f1 = f1 * mask[...,:,:]
        masked_f2 = f2 * mask[...,:,:]
        numerator = t.sqrt(calc_fidelity(masked_f1, masked_f2))
        denominator_f1 = t.sqrt(calc_fidelity(masked_f1, masked_f1))
        denominator_f2 = t.sqrt(calc_fidelity(masked_f2, masked_f2))
        frc.append(numerator / t.sqrt((denominator_f1 * denominator_f2)))

    frc = np.array(frc)

    # This moves from combined-image SNR to single-image SNR
    snr /= 2

    threshold = (snr + (2 * snr + 1) / np.sqrt(n_pix)) / \
        (1 + snr + (2 * np.sqrt(snr)) / np.sqrt(n_pix))

    if not im_np:
        bins = t.tensor(bins)
        frc = t.tensor(frc)
        threshold = t.tensor(threshold)

    return bins[:-1], frc, threshold

