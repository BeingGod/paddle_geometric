import copy
from abc import ABC, abstractmethod
from typing import Any, Tuple

import paddle
from paddle import Tensor

from paddle_geometric.data import Data
from paddle_geometric.transforms import BaseTransform
from paddle_geometric.utils import to_paddle_csc_tensor


class RootedSubgraphData(Data):
    r"""A data object describing a homogeneous graph together with each node's
    rooted subgraph.

    It contains several additional properties that hold the information to map
    to batch of every node's rooted subgraph:

    * :obj:`sub_edge_index` (Tensor): The edge indices of all combined rooted
      subgraphs.
    * :obj:`n_id` (Tensor): The indices of nodes in all combined rooted
      subgraphs.
    * :obj:`e_id` (Tensor): The indices of edges in all combined rooted
      subgraphs.
    * :obj:`n_sub_batch` (Tensor): The batch vector to distinguish nodes across
      different subgraphs.
    * :obj:`e_sub_batch` (Tensor): The batch vector to distinguish edges across
      different subgraphs.
    """
    def __inc__(self, key: str, value: Any, *args: Any, **kwargs: Any) -> Any:
        if key == 'sub_edge_index':
            return self.n_id.shape[0]
        if key in ['n_sub_batch', 'e_sub_batch']:
            return 1 + int(self.n_sub_batch[-1])
        elif key == 'n_id':
            return self.num_nodes
        elif key == 'e_id':
            assert self.edge_index is not None
            return self.edge_index.shape[1]
        return super().__inc__(key, value, *args, **kwargs)

    def map_data(self) -> Data:
        # Maps all feature information of the :class:`Data` object to each
        # rooted subgraph.
        data = copy.copy(self)

        for key, value in self.items():
            if key in ['sub_edge_index', 'n_id', 'e_id', 'e_sub_batch']:
                del data[key]
            elif key == 'n_sub_batch':
                continue
            elif key == 'num_nodes':
                data.num_nodes = self.n_id.shape[0]
            elif key == 'edge_index':
                data.edge_index = self.sub_edge_index
            elif self.is_node_attr(key):
                dim = self.__cat_dim__(key, value)
                data[key] = paddle.index_select(value, self.n_id, axis=dim)
            elif self.is_edge_attr(key):
                dim = self.__cat_dim__(key, value)
                data[key] = paddle.index_select(value, self.e_id, axis=dim)

        return data


class RootedSubgraph(BaseTransform, ABC):
    r"""Base class for implementing rooted subgraph transformations."""
    @abstractmethod
    def extract(
        self,
        data: Data,
    ) -> Tuple[Tensor, Tensor, Tensor, Tensor, Tensor]:
        # Returns the tuple:
        # :obj:`(sub_edge_index, n_id, e_id, n_sub_batch, e_sub_batch)`
        # of the :class:`RootedSubgraphData` object.
        pass

    def map(
        self,
        data: Data,
        n_mask: Tensor,
    ) -> Tuple[Tensor, Tensor, Tensor, Tensor, Tensor]:

        assert data.edge_index is not None
        num_nodes = data.num_nodes
        assert num_nodes is not None

        n_sub_batch, n_id = paddle.nonzero(n_mask, as_tuple=True)
        e_mask = n_mask[:, data.edge_index[0]] & n_mask[:, data.edge_index[1]]
        e_sub_batch, e_id = paddle.nonzero(e_mask, as_tuple=True)

        sub_edge_index = data.edge_index[:, e_id]
        arange = paddle.arange(n_id.shape[0], device=data.edge_index.place)
        node_map = paddle.ones([num_nodes, num_nodes], dtype='int64')
        node_map[n_sub_batch, n_id] = arange
        sub_edge_index += (arange * data.num_nodes)[e_sub_batch]
        sub_edge_index = paddle.reshape(node_map, [-1])[sub_edge_index]

        return sub_edge_index, n_id, e_id, n_sub_batch, e_sub_batch

    def forward(self, data: Data) -> RootedSubgraphData:
        out = self.extract(data)
        d = RootedSubgraphData.from_dict(data.to_dict())
        d.sub_edge_index, d.n_id, d.e_id, d.n_sub_batch, d.e_sub_batch = out
        return d


class RootedEgoNets(RootedSubgraph):
    r"""Collects rooted :math:`k`-hop EgoNets for each node in the graph, as
    described in the `"From Stars to Subgraphs: Uplifting Any GNN with Local
    Structure Awareness" <https://arxiv.org/abs/2110.03753>`_ paper.

    Args:
        num_hops (int): the number of hops :math:`k`.
    """
    def __init__(self, num_hops: int) -> None:
        super().__init__()
        self.num_hops = num_hops

    def extract(
        self,
        data: Data,
    ) -> Tuple[Tensor, Tensor, Tensor, Tensor, Tensor]:

        assert data.edge_index is not None
        num_nodes = data.num_nodes
        assert num_nodes is not None

        adj_t = to_paddle_csc_tensor(data.edge_index, size=data.size()).t()
        n_mask = paddle.eye(num_nodes, dtype='float32', place=data.edge_index.place)
        for _ in range(self.num_hops):
            n_mask += paddle.matmul(adj_t, n_mask)

        return self.map(data, n_mask > 0)

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(num_hops={self.num_hops})'


class RootedRWSubgraph(RootedSubgraph):
    """Collects rooted random-walk based subgraphs for each node in the graph,
    as described in the `"From Stars to Subgraphs: Uplifting Any GNN with Local
    Structure Awareness" <https://arxiv.org/abs/2110.03753>`_ paper.

    Args:
        walk_length (int): the length of the random walk.
        repeat (int, optional): The number of times of repeating the random
            walk to reduce randomness. (default: :obj:`1`)
    """
    def __init__(self, walk_length: int, repeat: int = 1):
        super().__init__()
        self.walk_length = walk_length
        self.repeat = repeat

    def extract(
        self,
        data: Data,
    ) -> Tuple[Tensor, Tensor, Tensor, Tensor, Tensor]:
        from paddle_geometric.utils import random_walk

        assert data.edge_index is not None
        num_nodes = data.num_nodes
        assert num_nodes is not None

        start = paddle.arange(num_nodes, dtype='int64', place=data.edge_index.place)
        start = paddle.tile(start.reshape([-1, 1]), [1, self.repeat]).reshape([-1])
        walk = random_walk(data.edge_index[0], data.edge_index[1], start,
                           self.walk_length, num_nodes=data.num_nodes)

        n_mask = paddle.zeros((num_nodes, num_nodes), dtype='bool', place=walk.place)
        start = paddle.tile(start.reshape([-1, 1]), [1, (self.walk_length + 1)]).reshape([-1])
        n_mask[start, walk.reshape([-1])] = True

        return self.map(data, n_mask)

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(walk_length={self.walk_length})'
