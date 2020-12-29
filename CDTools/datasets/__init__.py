""" This module contains all the datasets for interacting with ptychography data

All the access to data from standard ptychography and CDI experiments is 
coordinated through the various datasets defined in this module. They make use
of the lower-level data reading and writing functions defined in tools.data,
but critically all of these datasets subclass torch.Dataset. This allows
them to be used as standard torch datasets during reconstructions, which
helps make it easy to use the various data-handling strategies that are
implemented by default in pytorch (such as drawing data in a random order,
drawing minibatches, etc.)

New Datasets can be defined a subclass of the main CDataset class defined
in the base.py file. Example implementations of all these functions
can be found in the code for the Ptycho2DDataset class. In addition, it is
recommended to read through the tutorial section on defining a new CDI
dataset before attempting to do so

* __init__
* __len__
* _load
* to
* from_cxi
* to_cxi
* inspect

"""

from __future__ import division, print_function, absolute_import

__all__ = ['CDataset','Ptycho2DDataset']

from CDTools.datasets.base import CDataset
from CDTools.datasets.ptycho_2d_dataset import Ptycho2DDataset
