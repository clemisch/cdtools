import cdtools
from matplotlib import pyplot as plt

# First, we load an example dataset from a .cxi file
filename = 'example_data/lab_ptycho_data.cxi'
dataset = cdtools.datasets.Ptycho2DDataset.from_cxi(filename)

# Next, we create a ptychography model from the dataset
model = cdtools.models.SimplePtycho.from_dataset(dataset)

# Now, we run a short reconstruction from the dataset!
for loss in model.Adam_optimize(20, dataset):
    print(model.report())

# Finally, we plot the results
model.inspect(dataset)
model.compare(dataset)
plt.show()
