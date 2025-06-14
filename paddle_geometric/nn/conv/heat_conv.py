from typing import Optional

import paddle
import paddle.nn.functional as F
from paddle import Tensor, nn

from paddle_geometric.nn.conv import MessagePassing
from paddle_geometric.nn.dense.linear import HeteroLinear, Linear
from paddle_geometric.typing import Adj, OptTensor
from paddle_geometric.utils import softmax


class HEATConv(MessagePassing):
    r"""The heterogeneous edge-enhanced graph attentional operator from the
    `"Heterogeneous Edge-Enhanced Graph Attention Network For Multi-Agent
    Trajectory Prediction" <https://arxiv.org/abs/2106.07161>`_ paper.

    Args:
        in_channels (int): Size of each input sample, or :obj:`-1` to derive
            the size from the first input(s) to the forward method.
        out_channels (int): Size of each output sample.
        num_node_types (int): The number of node types.
        num_edge_types (int): The number of edge types.
        edge_type_emb_dim (int): The embedding size of edge types.
        edge_dim (int): Edge feature dimensionality.
        edge_attr_emb_dim (int): The embedding size of edge features.
        heads (int, optional): Number of multi-head-attentions.
            (default: :obj:`1`)
        concat (bool, optional): If set to :obj:`False`, the multi-head
            attentions are averaged instead of concatenated.
            (default: :obj:`True`)
        negative_slope (float, optional): LeakyReLU angle of the negative
            slope. (default: :obj:`0.2`)
        dropout (float, optional): Dropout probability of the normalized
            attention coefficients. (default: :obj:`0`)
        root_weight (bool, optional): If set to :obj:`False`, the layer will
            not add transformed root node features to the output.
            (default: :obj:`True`)
        bias (bool, optional): If set to :obj:`False`, the layer will not learn
            an additive bias. (default: :obj:`True`)
        **kwargs (optional): Additional arguments of
            :class:`paddle_geometric.nn.conv.MessagePassing`.
    """
    def __init__(self, in_channels: int, out_channels: int,
                 num_node_types: int, num_edge_types: int,
                 edge_type_emb_dim: int, edge_dim: int, edge_attr_emb_dim: int,
                 heads: int = 1, concat: bool = True,
                 negative_slope: float = 0.2, dropout: float = 0.0,
                 root_weight: bool = True, bias: bool = True, **kwargs):

        kwargs.setdefault('aggr', 'add')
        super().__init__(node_dim=0, **kwargs)

        self.in_channels = in_channels
        self.out_channels = out_channels
        self.heads = heads
        self.concat = concat
        self.negative_slope = negative_slope
        self.dropout = dropout
        self.root_weight = root_weight

        self.hetero_lin = HeteroLinear(in_channels, out_channels,
                                       num_node_types, bias=bias)

        self.edge_type_emb = nn.Embedding(num_edge_types, edge_type_emb_dim)
        self.edge_attr_emb = Linear(edge_dim, edge_attr_emb_dim, bias_attr=False)

        self.att = Linear(
            2 * out_channels + edge_type_emb_dim + edge_attr_emb_dim,
            self.heads, bias_attr=False)

        self.lin = Linear(out_channels + edge_attr_emb_dim, out_channels,
                          bias_attr=bias)

        self.reset_parameters()

    def reset_parameters(self):
        super().reset_parameters()
        self.hetero_lin.reset_parameters()
        self.edge_type_emb.weight.set_value(paddle.nn.initializer.XavierUniform()(self.edge_type_emb.weight.shape))
        self.edge_attr_emb.reset_parameters()
        self.att.reset_parameters()
        self.lin.reset_parameters()

    def forward(self, x: Tensor, edge_index: Adj, node_type: Tensor,
                edge_type: Tensor, edge_attr: OptTensor = None) -> Tensor:

        x = self.hetero_lin(x, node_type)

        edge_type_emb = F.leaky_relu(self.edge_type_emb(edge_type),
                                     self.negative_slope)

        # propagate_type: (x: Tensor, edge_type_emb: Tensor,
        #                  edge_attr: OptTensor)
        out = self.propagate(edge_index, x=x, edge_type_emb=edge_type_emb,
                             edge_attr=edge_attr)

        if self.concat:
            if self.root_weight:
                out = out + x.reshape([-1, 1, self.out_channels])
            out = out.reshape([-1, self.heads * self.out_channels])
        else:
            out = out.mean(axis=1)
            if self.root_weight:
                out = out + x

        return out

    def message(self, x_i: Tensor, x_j: Tensor, edge_type_emb: Tensor,
                edge_attr: Tensor, index: Tensor, ptr: OptTensor,
                size_i: Optional[int]) -> Tensor:

        edge_attr = F.leaky_relu(self.edge_attr_emb(edge_attr),
                                 self.negative_slope)

        alpha = paddle.concat([x_i, x_j, edge_type_emb, edge_attr], axis=-1)
        alpha = F.leaky_relu(self.att(alpha), self.negative_slope)
        alpha = softmax(alpha, index, ptr, size_i)
        alpha = F.dropout(alpha, p=self.dropout, training=self.training)

        out = self.lin(paddle.concat([x_j, edge_attr], axis=-1)).unsqueeze(-2)
        return out * alpha.unsqueeze(-1)

    def __repr__(self) -> str:
        return (f'{self.__class__.__name__}({self.in_channels}, '
                f'{self.out_channels}, heads={self.heads})')
