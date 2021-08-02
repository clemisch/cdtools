from __future__ import division, print_function, absolute_import
import numpy as np
import torch as t
from copy import copy
import h5py
import pathlib
from CDTools.datasets import CDataset, Ptycho2DDataset, PolarizedPtycho2DDataset
from CDTools.tools import data as cdtdata, initializers, polarization, interactions
from CDTools.tools.polarization import apply_jones_matrix as jones
from CDTools.tools import plotting
from torch.utils import data as torchdata
from matplotlib import pyplot as plt
from matplotlib.widgets import Slider
from matplotlib import ticker
from CDTools.models import FancyPtycho, PolarizedFancyPtycho
from torch.utils import data as torchdata
from math import cos as cos, sin as sin
import math

dataset = PolarizedPtycho2DDataset.from_cxi('test_ptycho.cxi')
model = PolarizedFancyPtycho.from_dataset(dataset)

def test_apply_jones_matrix(multiple_modes=False, multiple_patterns=False, diff_jones_across_pixels=False, transpose=True, mode=0):

	angle = 87
	angle_2 = angle - 45

	def polarizer(angle):
		theta = math.radians(angle)
		polarizer = t.tensor([[(cos(theta)) ** 2, sin(2 * theta) / 2], [sin(2 * theta) / 2, sin(theta) ** 2]]).to(dtype=t.cfloat)
		return polarizer
	exponent = t.exp(-1j * math.pi / 4 * t.ones(2, 2))
	theta2 = math.radians(angle_2)
	quarter_plate = t.tensor([[(cos(theta2))**2 + 1j * (sin(theta2))**2, (1 - 1j) * sin(theta2) * cos(theta2)], 
									   [(1 - 1j) * sin(theta2) * cos(theta2), (sin(theta2))**2 + 1j * (cos(theta2))**2]])

	def build_from_quarters(jones1, jones2, jones3, jones4):
		x = t.cat((t.stack((jones1, jones1), dim=-1), t.stack((jones2, jones2), dim=-1)), dim=-1)
		y = t.cat((t.stack((jones3, jones3), dim=-1), t.stack((jones4, jones4), dim=-1)), dim=-1)
		x = t.stack((x, x), dim=-2)
		y = t.stack((y, y), dim=-2)
		return t.cat((x, y), dim=-2).to(dtype=t.cfloat)

	jones_plate = t.matmul(quarter_plate, polarizer(angle))
	jones0 = polarizer(0)
	jones90 = polarizer(90)
	jones45 = polarizer(45)

	'''	
	after applying the polarizer and the quarter_plate, the probe should get circularly polarized
	probe: no multiple modes, 1 diffr pattern
		2xMxL
	jones_matrix: same jones matrix applied to all the pixels
		2x2
	'''
	if not multiple_modes and not multiple_patterns and not diff_jones_across_pixels:
		probe = t.rand(2, 4, 4, dtype=t.cfloat)
		out = jones(jones(probe, polarizer(angle), multiple_modes=False, transpose=transpose), quarter_plate, multiple_modes=False, transpose=transpose, mode=0)
		
		print('expected shape:(2, 3, 4)')
		print('actual:', out.shape)
		print('simulated:', out)

	'''probe: no multiple modes, 1 diffr pattern
		2xMxL = 2x4x4
	jones_matrix: jones matrices differ from pixel to pixel
		2x2xMxL = 2x2x4x4
	4 quarters: 
		1: [:, :, :-2, :-2] - circular_polarizer, 
		2: [:, :, :-2, -2:] - 0
		3: [:, :, -2:, :-2] - 90
		4: [:, :, -2:, -2:] - 45
	'''
	if not multiple_modes and not multiple_patterns and diff_jones_across_pixels:

		jones_matr = build_from_quarters(jones_plate, jones0, jones90, jones45)
		probe = t.ones(2, 4, 4).to(dtype=t.cfloat)
		print('jones:', jones_matr)
		# print('jones:', jones_matr)
		out = jones(probe, jones_matr, multiple_modes=False, transpose=transpose)

		print('expected shape:(2, 4, 4)')
		print('simulated:', out_simulated.shape)
		print('simulated:', out)


	'''
	probe: no multiple modes, multiple diffr patterns
		Nx2xMxL = 3x2x4x4
	jones_matrix: same jones matrix applied to all the pixels
		Nx2x2 = 3x2x2
		3 different matrices for each probe:
			1: 0
			2: 45
			3: 90
	'''
	if not multiple_modes and multiple_patterns and not diff_jones_across_pixels:
		probe = t.ones(2, 4, 4, dtype=t.cfloat)
		probe = t.stack(([probe * (i + 1) for i in range(3)]), dim=0)
		jones_matr = t.stack(([polarizer(angle) for angle in [0, 45, 90]]), dim=0)
		print('probe shape:', probe.shape, 'jones shape', jones_matr.shape)
		out = jones(probe, jones_matr, multiple_modes=False, transpose=transpose)

		print('expected shape: (3, 2, 4, 4)')
		print('actual:', out.shape)
		print('simulated:', out)

	'''
	probe: no multiple modes, multiple diffr pattern
		Nx2xMxL = 3x2x4x4 
	jones_matrix: jones matrices differ from pixel to pixel
		Nx2x2xMxL = 3x2x2x4x4
		jones matrix for the 1st pattern:
			1: quat plate, 2: 90, 3: 0, 4: 45
		jones matrix for the 2nd pattern:
			1: 0, 2: 45, 3: 90, 4: quat plate
		jones matrix for the 3rd pattern:
		 	1: 90, 2: 0, 3: 45, 4: quat plate
	'''
	if not multiple_modes and multiple_patterns and diff_jones_across_pixels:
		jones_m = [build_from_quarters(jones_plate, jones90, jones0, jones45), 
									 build_from_quarters(jones0, jones45, jones90, jones_plate),
									 build_from_quarters(jones90, jones0, jones45, jones_plate)]
		jones_matr = t.stack(([i for i in jones_m]), dim=0)
		probe = t.ones(2, 4, 4, dtype=t.cfloat)
		probe = t.stack(([probe * (i + 1) for i in range(3)]), dim=0)
		out = jones(probe, jones_matr, multiple_modes=False, transpose=transpose)

		print('expected shape: (3, 2, 4, 4)')
		print('actual shape:', out.shape)
		print('simulated:', out)

	'''
	probe: multiple modes, 1 diffr pattern
		Px2xMxL = 2x2x3x4
	jones_matrix: same jones matrix applied to all the pixels
		2x2 - quarter plate
			Nx2xMxL = 3x2x4x4
	'''
	if multiple_modes and not multiple_patterns and not diff_jones_across_pixels:
		probe = t.rand(2, 2, 3, 4, dtype=t.cfloat)
		out = jones(jones(probe, polarizer(angle), multiple_modes=True, transpose=transpose), quarter_plate, multiple_modes=True, transpose=transpose)

		print('expected shape: (2, 2, 3, 4)')
		print('actual:', out.shape)
		print('simulated:', out)

	'''
	probe: multiple modes, 1 diffr pattern
		Px2xMxL = 3x2x4x4 
	jones_matrix: jones matrices differ from pixel to pixel
		2xMxL = 2x4x4
	4 quarters: 
		1: [:, :, :-2, :-2] - circular_polarizer, 
		2: [:, :, :-2, -2:] - 0
		3: [:, :, -2:, :-2] - 90
		4: [:, :, -2:, -2:] - 45
	'''
	if multiple_modes and not multiple_patterns and diff_jones_across_pixels:
		jones_matr = build_from_quarters(jones_plate, jones0, jones90, jones45)
		probe = t.ones(2, 4, 4, dtype=t.cfloat)
		probe = t.stack(([probe * (i + 1) for i in range(3)]), dim=0)
		out = jones(probe, jones_matr, multiple_modes=True, transpose=transpose)

		print('expected shape: (3, 2, 4, 4)')
		print('actual shape:', out.shape)
		print('simulated:', out)


	'''
	probe: multiple modes, multiple diffr patterns
		NxPx2xMxL = 3x2x2x3x4
	jones_matrix: same jones matrix applied to all the pixels (although differs from pattern to pattern)
		Nx2x2 = 3x2x2
			3 different matrices for each probe in one mode:
			1: 0
			2: 45
			3: 90
	'''
	if multiple_modes and multiple_patterns and not diff_jones_across_pixels:
		probe = t.ones(2, 3, 4, dtype=t.cfloat)
		# 1st mode
		probe_mode1 = t.stack(([probe * (i + 1) for i in range(3)]), dim=0)
		# 2nd mode
		probe_mode2 = 10 * t.stack(([probe * (i + 1) for i in range(3)]), dim=0)	
		probe = t.stack((probe_mode1, probe_mode2), dim=1)
		jones_matr = t.stack(([polarizer(angle) for angle in [0, 45, 90]]), dim=0)
		print('probe shape:', probe.shape, 'jones shape', jones_matr.shape)
		out = jones(probe, jones_matr, multiple_modes=True, transpose=transpose)

		print('expected shape: (3, 2, 2, 3, 4)')
		print('actual:', out.shape)
		print('simulated (patterns in one mode):', out[:, mode, :, :, :])


	'''
	probe: multiple modes, multiple diffr pattern
		NxPx2xMxL = 3x4x2x4x4 
	jones_matrix: jones matrices differ from pixel to pixel
		NxPx2x2xMxL = 3x2x2x2x4x4
		(differs across the patterns in each mode)
		jones matrix for the 1st pattern:
			1: quat plate, 2: 90, 3: 0, 4: 45
		jones matrix for the 2nd pattern:
			1: 0, 2: 45, 3: 90, 4: quat plate
		jones matrix for the 3rd pattern:
		 	1: 90, 2: 0, 3: 45, 4: quat plate
	'''
	if multiple_modes and multiple_patterns and diff_jones_across_pixels:
		jones_m = [build_from_quarters(jones_plate, jones90, jones0, jones45), 
							 build_from_quarters(jones0, jones45, jones90, jones_plate),
							 build_from_quarters(jones90, jones0, jones45, jones_plate)]
		jones_matr = t.stack(([i for i in jones_m]), dim=0)
		probe = t.ones(2, 4, 4, dtype=t.cfloat)
		# one mode
		probe_mode = t.stack(([probe * (i + 1) for i in range(3)]), dim=0)		
		probe = t.stack(([probe_mode * (10 ** i) for i in range(4)]), dim=1)
		print('probe:', probe.shape)
		print('jones:', jones_matr.shape)
		out = jones(probe, jones_matr, multiple_modes=True, transpose=transpose)

		print('expected shape: (3, 2, 2, 4, 4)')
		print('actual shape:', out.shape)
		print('simulated patterns in one mode:', out[:, mode, :, :, :])


	return None

probe = t.rand(2, 3, 4, dtype=t.cfloat)
angle = 57
angle_2 = angle - 45
theta = math.radians(angle)
polarizer = t.tensor([[(cos(theta)) ** 2, sin(2 * theta) / 2], [sin(2 * theta) / 2, sin(theta) ** 2]]).to(dtype=t.cfloat)
exponent = t.exp(-1j * math.pi / 4 * t.ones(2, 2))
theta2 = math.radians(angle_2)
quarter_plate = t.tensor([[(cos(theta2))**2 + 1j * (sin(theta2))**2, (1 - 1j) * sin(theta2) * cos(theta2)], 
								   [(1 - 1j) * sin(theta2) * cos(theta2), (sin(theta2))**2 + 1j * (cos(theta2))**2]])

# pol = t.matmul(polarizer, probe)
# pl = t.matmul(quarter_plate, pol)
# print(pol)

# print(pl)
# pol = pol.unsqueeze(-1).unsqueeze(-1)
# print(polarization.apply_jones_matrix(polarization.apply_jones_matrix(probe, polarizer), quarter_plate, multiple_modes=False))
test_apply_jones_matrix(multiple_modes=True, multiple_patterns=True, diff_jones_across_pixels=True, mode=2)