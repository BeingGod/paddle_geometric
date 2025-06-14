from typing import Optional

import paddle
import paddle.nn.functional as F
from paddle import Tensor
from paddle.nn import Layer
from paddle_geometric.nn.conv import MessagePassing
from paddle_geometric.typing import Adj, OptTensor, SparseTensor
from paddle_geometric.utils import add_self_loops, remove_self_loops, softmax


class AGNNConv(MessagePassing):
    r"""The graph attentional propagation layer from the
    `"Attention-based Graph Neural Network for Semi-Supervised Learning"
    <https://arxiv.org/abs/1803.03735>`_ paper.

    .. math::
        \mathbf{X}^{\prime} = \mathbf{P} \mathbf{X},

    where the propagation matrix :math:`\mathbf{P}` is computed as

    .. math::
        P_{i,j} = \frac{\exp( \beta \cdot \cos(\mathbf{x}_i, \mathbf{x}_j))}
        {\sum_{k \in \mathcal{N}(i)\cup \{ i \}} \exp( \beta \cdot
        \cos(\mathbf{x}_i, \mathbf{x}_k))}

    with trainable parameter :math:`\beta`.

    Args:
        requires_grad (bool, optional): If set to :obj:`False`, :math:`\beta`
            will not be trainable. (default: :obj:`True`)
        add_self_loops (bool, optional): If set to :obj:`False`, will not add
            self-loops to the input graph. (default: :obj:`True`)
        **kwargs (optional): Additional arguments of
            :class:`paddle_geometric.nn.conv.MessagePassing`.

    Shapes:
        - **input:**
          node features :math:`(|\mathcal{V}|, F)`,
          edge indices :math:`(2, |\mathcal{E}|)`
        - **output:** node features :math:`(|\mathcal{V}|, F)`
    """
    def __init__(self, requires_grad: bool = True, add_self_loops: bool = True,
                 **kwargs):
        kwargs.setdefault('aggr', 'add')
        super().__init__(**kwargs)

        self.requires_grad = requires_grad
        self.add_self_loops = add_self_loops

        if requires_grad:
            self.beta = self.create_parameter(shape=[1])
        else:
            self.register_buffer('beta', paddle.ones([1]))

        self.reset_parameters()

    def reset_parameters(self):
        super().reset_parameters()
        if self.requires_grad:
            self.beta.set_value(paddle.ones([1]))

    def forward(self, x: Tensor, edge_index: Adj) -> Tensor:
        if self.add_self_loops:
            if isinstance(edge_index, Tensor):
                edge_index, _ = remove_self_loops(edge_index)
                edge_index, _ = add_self_loops(edge_index, num_nodes=x.shape[self.node_dim])
            elif isinstance(edge_index, SparseTensor):
                edge_index = edge_index.set_diag()

        x_norm = F.normalize(x, p=2., axis=-1)

        # propagate_type: (x: Tensor, x_norm: Tensor)
        return self.propagate(edge_index, x=x, x_norm=x_norm)

    def message(self, x_j: Tensor, x_norm_i: Tensor, x_norm_j: Tensor,
                index: Tensor, ptr: OptTensor,
                size_i: Optional[int]) -> Tensor:
        alpha = self.beta * (x_norm_i * x_norm_j).sum(axis=-1)
        alpha = softmax(alpha, index, ptr, size_i)
        return x_j * alpha.unsqueeze(-1)
