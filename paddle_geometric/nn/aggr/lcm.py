from math import ceil, log2
from typing import Optional
import paddle
from paddle import Tensor
from paddle.nn import GRUCell, Linear

from paddle_geometric.nn.aggr import Aggregation


class LCMAggregation(Aggregation):
    r"""The Learnable Commutative Monoid aggregation from the
    `"Learnable Commutative Monoids for Graph Neural Networks"
    <https://arxiv.org/abs/2212.08541>`_ paper, in which the elements are
    aggregated using a binary tree reduction with
    :math:`\mathcal{O}(\log |\mathcal{V}|)` depth.

    .. note::

        :class:`LCMAggregation` requires sorted indices :obj:`index` as input.
        Specifically, if you use this aggregation as part of
        :class:`~paddle_geometric.nn.conv.MessagePassing`, ensure that
        :obj:`edge_index` is sorted by destination nodes, either by manually
        sorting edge indices or by calling `Data.sort()`.

    .. warning::

        :class:`LCMAggregation` is not a permutation-invariant operator.

    Args:
        in_channels (int): Size of each input sample.
        out_channels (int): Size of each output sample.
        project (bool, optional): If set to :obj:`True`, the layer will apply a
            linear transformation followed by an activation function before
            aggregation. (default: :obj:`True`)
    """
    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        project: bool = True,
    ):
        super().__init__()

        if in_channels != out_channels and not project:
            raise ValueError(f"Inputs of '{self.__class__.__name__}' must be "
                             f"projected if `in_channels != out_channels`")

        self.in_channels = in_channels
        self.out_channels = out_channels
        self.project = project

        if self.project:
            self.lin = Linear(in_channels, out_channels)
        else:
            self.lin = None

        self.gru_cell = GRUCell(out_channels, out_channels)

    def reset_parameters(self):
        if self.project:
            self.lin.reset_parameters()
        self.gru_cell.reset_parameters()

    def forward(
        self,
        x: Tensor,
        index: Optional[Tensor] = None,
        ptr: Optional[Tensor] = None,
        dim_size: Optional[int] = None,
        dim: int = -2,
        max_num_elements: Optional[int] = None,
    ) -> Tensor:

        if self.project:
            x = paddle.nn.functional.relu(self.lin(x))

        x, _ = self.to_dense_batch(x, index, ptr, dim_size, dim,
                                   max_num_elements=max_num_elements)

        x = x.transpose([1, 0, 2])  # [num_neighbors, num_nodes, num_features]
        _, num_nodes, num_features = x.shape

        depth = ceil(log2(x.shape[0]))
        for _ in range(depth):
            half_size = ceil(x.shape[0] / 2)

            if x.shape[0] % 2 == 1:
                # This level of the tree has an odd number of nodes, so the
                # remaining unmatched node gets moved to the next level.
                x, remainder = x[:-1], x[-1:]
            else:
                remainder = None

            left_right = x.reshape([-1, 2, num_nodes, num_features])
            right_left = left_right.flip(axis=[1])

            left_right = left_right.reshape([-1, num_features])
            right_left = right_left.reshape([-1, num_features])

            # Execute the GRUCell for all (left, right) pairs in the current
            # level of the tree in parallel:
            out = self.gru_cell(left_right, right_left)
            out = out.reshape([-1, 2, num_nodes, num_features])
            out = out.mean(axis=1)
            if remainder is not None:
                out = paddle.concat([out, remainder], axis=0)

            x = out.reshape([half_size, num_nodes, num_features])

        assert x.shape[0] == 1
        return x.squeeze(0)

    def __repr__(self) -> str:
        return (f'{self.__class__.__name__}({self.in_channels}, '
                f'{self.out_channels}, project={self.project})')
