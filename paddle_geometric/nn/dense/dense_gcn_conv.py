from typing import Optional

import paddle
import paddle.nn.functional as F
from paddle import Tensor
from paddle.nn import Layer, Linear

class DenseGCNConv(Layer):
    r"""See :class:`paddle_geometric.nn.conv.GCNConv`."""
    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        improved: bool = False,
        bias: bool = True,
    ):
        super().__init__()

        self.in_channels = in_channels
        self.out_channels = out_channels
        self.improved = improved

        self.lin = Linear(in_channels, out_channels, weight_attr=paddle.framework.ParamAttr(initializer=paddle.nn.initializer.XavierUniform()))

        if bias:
            self.bias = self.create_parameter([out_channels])
        else:
            self.bias = None

        self.reset_parameters()

    def reset_parameters(self):
        r"""Resets all learnable parameters of the module."""
        if self.bias is not None:
            paddle.nn.initializer.Constant(0)(self.bias)

    def forward(self, x: Tensor, adj: Tensor, mask: Optional[Tensor] = None,
                add_loop: bool = True) -> Tensor:
        r"""Forward pass.

        Args:
            x (Tensor): Node feature tensor
                :math:`\mathbf{X} \in \mathbb{R}^{B \times N \times F}`, with
                batch-size :math:`B`, (maximum) number of nodes :math:`N` for
                each graph, and feature dimension :math:`F`.
            adj (Tensor): Adjacency tensor
                :math:`\mathbf{A} \in \mathbb{R}^{B \times N \times N}`.
                The adjacency tensor is broadcastable in the batch dimension,
                resulting in a shared adjacency matrix for the complete batch.
            mask (Tensor, optional): Mask matrix
                :math:`\mathbf{M} \in {\{ 0, 1 \}}^{B \times N}` indicating
                the valid nodes for each graph. (default: :obj:`None`)
            add_loop (bool, optional): If set to :obj:`False`, the layer will
                not automatically add self-loops to the adjacency matrices.
                (default: :obj:`True`)
        """
        x = x.unsqueeze(0) if x.ndim == 2 else x
        adj = adj.unsqueeze(0) if adj.ndim == 2 else adj
        B, N, _ = adj.shape

        if add_loop:
            adj = adj.clone()
            idx = paddle.arange(N, dtype=paddle.int64)
            adj[:, idx, idx] = 1 if not self.improved else 2

        out = self.lin(x)
        deg_inv_sqrt = adj.sum(axis=-1).clip(min=1).pow(-0.5)

        adj = deg_inv_sqrt.unsqueeze(-1) * adj * deg_inv_sqrt.unsqueeze(-2)
        out = paddle.matmul(adj, out)

        if self.bias is not None:
            out = out + self.bias

        if mask is not None:
            out = out * mask.reshape([B, N, 1]).astype(x.dtype)

        return out

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}({self.in_channels}, {self.out_channels})'
