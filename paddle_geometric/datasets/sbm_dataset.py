import os.path as osp
from typing import Any, Callable, List, Optional, Union

import numpy as np
import paddle
from paddle import Tensor

from paddle_geometric.data import Data, InMemoryDataset
from paddle_geometric.utils import stochastic_blockmodel_graph


class StochasticBlockModelDataset(InMemoryDataset):
    r"""A synthetic graph dataset generated by the stochastic block model.
    The node features of each block are sampled from normal distributions where
    the centers of clusters are vertices of a hypercube, as computed by the
    :meth:`sklearn.datasets.make_classification` method.
    """

    def __init__(
        self,
        root: str,
        block_sizes: Union[List[int], Tensor],
        edge_probs: Union[List[List[float]], Tensor],
        num_graphs: int = 1,
        num_channels: Optional[int] = None,
        is_undirected: bool = True,
        transform: Optional[Callable] = None,
        pre_transform: Optional[Callable] = None,
        force_reload: bool = False,
        **kwargs: Any,
    ) -> None:
        if not isinstance(block_sizes, paddle.Tensor):
            block_sizes = paddle.to_tensor(block_sizes, dtype=paddle.int64)
        if not isinstance(edge_probs, paddle.Tensor):
            edge_probs = paddle.to_tensor(edge_probs, dtype=paddle.float32)

        assert num_graphs > 0

        self.block_sizes = block_sizes
        self.edge_probs = edge_probs
        self.num_graphs = num_graphs
        self.num_channels = num_channels
        self.is_undirected = is_undirected

        self.kwargs = {
            'n_informative': num_channels,
            'n_redundant': 0,
            'flip_y': 0.0,
            'shuffle': False,
        }
        self.kwargs.update(kwargs)

        super().__init__(root, transform, pre_transform,
                         force_reload=force_reload)
        self.load(self.processed_paths[0])

    @property
    def processed_dir(self) -> str:
        return osp.join(self.root, self.__class__.__name__, 'processed')

    @property
    def processed_file_names(self) -> str:
        block_sizes = self.block_sizes.numpy().tolist()
        hash1 = '-'.join([f'{x:.1f}' for x in block_sizes])

        edge_probs = self.edge_probs.numpy().tolist()
        hash2 = '-'.join([f'{x:.1f}' for x in edge_probs])

        return f'data_{self.num_channels}_{hash1}_{hash2}_{self.num_graphs}.pt'

    def process(self) -> None:
        from sklearn.datasets import make_classification

        edge_index = stochastic_blockmodel_graph(
            self.block_sizes, self.edge_probs, directed=not self.is_undirected)

        num_samples = int(self.block_sizes.sum())
        num_classes = self.block_sizes.shape[0]

        data_list = []
        for _ in range(self.num_graphs):
            x = None
            if self.num_channels is not None:
                x, y_not_sorted = make_classification(
                    n_samples=num_samples,
                    n_features=self.num_channels,
                    n_classes=num_classes,
                    weights=(self.block_sizes / num_samples).numpy(),
                    **self.kwargs,
                )
                x = x[np.argsort(y_not_sorted)]
                x = paddle.to_tensor(x, dtype=paddle.float32)

            y = paddle.arange(num_classes).repeat_interleave(self.block_sizes)

            data = Data(x=x, edge_index=edge_index, y=y)

            if self.pre_transform is not None:
                data = self.pre_transform(data)

            data_list.append(data)

        self.save(data_list, self.processed_paths[0])


class RandomPartitionGraphDataset(StochasticBlockModelDataset):
    r"""The random partition graph dataset from the `"How to Find Your
    Friendly Neighborhood: Graph Attention Design with Self-Supervision"
    <https://openreview.net/forum?id=Wi5KUNlqWty>`_ paper.
    """

    def __init__(
        self,
        root: str,
        num_classes: int,
        num_nodes_per_class: int,
        node_homophily_ratio: float,
        average_degree: float,
        num_graphs: int = 1,
        num_channels: Optional[int] = None,
        is_undirected: bool = True,
        transform: Optional[Callable] = None,
        pre_transform: Optional[Callable] = None,
        **kwargs: Any,
    ) -> None:

        self._num_classes = num_classes
        self.num_nodes_per_class = num_nodes_per_class
        self.node_homophily_ratio = node_homophily_ratio
        self.average_degree = average_degree

        ec_over_v2 = average_degree / num_nodes_per_class
        p_in = node_homophily_ratio * ec_over_v2
        p_out = (ec_over_v2 - p_in) / (num_classes - 1)

        block_sizes = [num_nodes_per_class for _ in range(num_classes)]
        edge_probs = [[p_out for _ in range(num_classes)]
                      for _ in range(num_classes)]
        for r in range(num_classes):
            edge_probs[r][r] = p_in

        super().__init__(root, block_sizes, edge_probs, num_graphs,
                         num_channels, is_undirected, transform, pre_transform,
                         **kwargs)

    @property
    def processed_file_names(self) -> str:
        return (f'data_{self.num_channels}_{self._num_classes}_'
                f'{self.num_nodes_per_class}_{self.node_homophily_ratio:.1f}_'
                f'{self.average_degree:.1f}_{self.num_graphs}.pt')

    def process(self) -> None:
        return super().process()
