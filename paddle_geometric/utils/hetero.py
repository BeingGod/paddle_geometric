from typing import Dict, List, Optional, Set, Tuple, Union

import paddle
from paddle import Tensor
from paddle.nn import LayerDict

from paddle_geometric.typing import Adj, EdgeType, NodeType, SparseTensor
from paddle_geometric.utils import is_sparse, to_edge_index
from paddle_geometric.utils.num_nodes import maybe_num_nodes_dict


def group_hetero_graph(
    edge_index_dict: Dict[EdgeType, Tensor],
    num_nodes_dict: Optional[Dict[NodeType, int]] = None,
) -> Tuple[
        Tensor,
        Tensor,
        Tensor,
        Tensor,
        Dict[Union[str, int], Tensor],
        Dict[Union[NodeType, EdgeType], int],
]:
    num_nodes_dict = maybe_num_nodes_dict(edge_index_dict, num_nodes_dict)

    tmp = list(edge_index_dict.values())[0]

    key2int: Dict[Union[NodeType, EdgeType], int] = {}

    cumsum, offset = 0, {}  # Helper data.
    node_types, local_node_indices = [], []
    local2global: Dict[Union[str, int], Tensor] = {}
    for i, (key, N) in enumerate(num_nodes_dict.items()):
        key2int[key] = i
        node_types.append(paddle.full((N,), i, dtype=tmp.dtype))
        local_node_indices.append(paddle.arange(N, dtype=tmp.dtype))
        offset[key] = cumsum
        local2global[key] = local_node_indices[-1] + cumsum
        local2global[i] = local2global[key]
        cumsum += N

    node_type = paddle.concat(node_types, axis=0)
    local_node_idx = paddle.concat(local_node_indices, axis=0)

    edge_indices, edge_types = [], []
    for i, (keys, edge_index) in enumerate(edge_index_dict.items()):
        key2int[keys] = i
        inc = paddle.to_tensor([offset[keys[0]], offset[keys[-1]]], dtype=tmp.dtype).reshape([2, 1])
        edge_indices.append(edge_index + inc)
        edge_types.append(paddle.full((edge_index.shape[1],), i, dtype=tmp.dtype))

    edge_index = paddle.concat(edge_indices, axis=-1)
    edge_type = paddle.concat(edge_types, axis=0)

    return (
        edge_index,
        edge_type,
        node_type,
        local_node_idx,
        local2global,
        key2int,
    )


def get_unused_node_types(node_types: List[NodeType],
                          edge_types: List[EdgeType]) -> Set[NodeType]:
    dst_node_types = {edge_type[-1] for edge_type in edge_types}
    return set(node_types) - set(dst_node_types)


def check_add_self_loops(
    module: paddle.nn.Layer,
    edge_types: List[EdgeType],
) -> None:
    is_bipartite = any([key[0] != key[-1] for key in edge_types])
    if is_bipartite and getattr(module, 'add_self_loops', False):
        raise ValueError(
            f"'add_self_loops' attribute set to 'True' on module '{module}' "
            f"for use with edge type(s) '{edge_types}'. This will lead to "
            f"incorrect message passing results.")


def construct_bipartite_edge_index(
    edge_index_dict: Dict[EdgeType, Adj],
    src_offset_dict: Dict[EdgeType, int],
    dst_offset_dict: Dict[NodeType, int],
    edge_attr_dict: Optional[Dict[EdgeType, Tensor]] = None,
    num_nodes: Optional[int] = None,
) -> Tuple[Adj, Optional[Tensor]]:
    """Constructs a tensor of edge indices by concatenating edge indices
    for each edge type. The edge indices are increased by the offset of the
    source and destination nodes.
    """
    is_sparse_tensor = False
    edge_indices: List[Tensor] = []
    edge_attrs: List[Tensor] = []
    for edge_type, src_offset in src_offset_dict.items():
        edge_index = edge_index_dict[edge_type]
        dst_offset = dst_offset_dict[edge_type[-1]]

        is_sparse_tensor = isinstance(edge_index, SparseTensor)
        if is_sparse(edge_index):
            edge_index, _ = to_edge_index(edge_index)
            edge_index = paddle.flip(edge_index, [0])
        else:
            edge_index = edge_index.clone()

        edge_index[0] += src_offset
        edge_index[1] += dst_offset
        edge_indices.append(edge_index)

        if edge_attr_dict is not None:
            if isinstance(edge_attr_dict, LayerDict):
                value = edge_attr_dict['__'.join(edge_type)]
            else:
                value = edge_attr_dict[edge_type]
            if value.shape[0] != edge_index.shape[1]:
                value = paddle.expand(value, shape=[edge_index.shape[1], -1])
            edge_attrs.append(value)

    edge_index = paddle.concat(edge_indices, axis=1)

    edge_attr: Optional[Tensor] = None
    if edge_attr_dict is not None:
        edge_attr = paddle.concat(edge_attrs, axis=0)

    if is_sparse_tensor:
        edge_index = SparseTensor(
            row=edge_index[1],
            col=edge_index[0],
            value=edge_attr,
            sparse_sizes=(num_nodes, num_nodes),
        )

    return edge_index, edge_attr
