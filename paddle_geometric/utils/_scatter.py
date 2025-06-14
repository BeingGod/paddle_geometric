from typing import List, Optional, Tuple, Union

import paddle
from paddle import Tensor

import paddle_geometric.typing
from paddle_geometric import is_compiling, is_in_onnx_export, warnings
from paddle_geometric.typing import paddle_scatter
from paddle_geometric.utils.functions import cumsum

if paddle_geometric.typing.WITH_PADDLE_DEV:  # pragma: no cover

    warnings.filterwarnings('ignore', '.*is in beta and the API may change.*')

    def scatter(
        src: Tensor,
        index: Tensor,
        dim: int = 0,
        dim_size: Optional[int] = None,
        reduce: str = 'sum',
    ) -> Tensor:
        r"""Reduces all values from the :obj:`src` tensor at the indices
        specified in the :obj:`index` tensor along a given dimension
        :obj:`dim`. See the `documentation
        <https://paddle-scatter.readthedocs.io/en/latest/functions/
        scatter.html>`__ of the :obj:`torch_scatter` package for more
        information.

        Args:
            src (torch.Tensor): The source tensor.
            index (torch.Tensor): The index tensor.
            dim (int, optional): The dimension along which to index.
                (default: :obj:`0`)
            dim_size (int, optional): The size of the output tensor at
                dimension :obj:`dim`. If set to :obj:`None`, will create a
                minimal-sized output tensor according to
                :obj:`index.max() + 1`. (default: :obj:`None`)
            reduce (str, optional): The reduce operation (:obj:`"sum"`,
                :obj:`"mean"`, :obj:`"mul"`, :obj:`"min"` or :obj:`"max"`,
                :obj:`"any"`). (default: :obj:`"sum"`)
        """
        if isinstance(index, Tensor) and index.dim() != 1:
            raise ValueError(f"The `index` argument must be one-dimensional "
                             f"(got {index.dim()} dimensions)")

        dim = src.dim() + dim if dim < 0 else dim

        if isinstance(src, Tensor) and (dim < 0 or dim >= src.dim()):
            raise ValueError(f"The `dim` argument must lay between 0 and "
                             f"{src.dim() - 1} (got {dim})")

        if dim_size is None:
            dim_size = int(index.max()) + 1 if index.numel() > 0 else 0

        # For now, we maintain various different code paths, based on whether
        # the input requires gradients and whether it lays on the CPU/GPU.
        # For example, `torch_scatter` is usually faster than
        # `torch.scatter_reduce` on GPU, while `torch.scatter_reduce` is faster
        # on CPU.
        # `torch.scatter_reduce` has a faster forward implementation for
        # "min"/"max" reductions since it does not compute additional arg
        # indices, but is therefore way slower in its backward implementation.
        # More insights can be found in `test/utils/test_scatter.py`.

        size = list(src.shape[:dim]) + [dim_size] + list(src.shape[dim + 1:])

        # For "any" reduction, we use regular `put_along_axis_`:
        if reduce == 'any':
            index = broadcast(index, src, dim)
            return paddle.zeros(size).put_along_axis_(axis=dim, indices=index, values=src)

        # For "sum" and "mean" reduction, we make use of `put_along_axis_`:
        if reduce == 'sum' or reduce == 'add':
            index = broadcast(index, src, dim)
            return paddle.zeros(size).put_along_axis_(axis=dim, indices=index, values=src)

        if reduce == 'mean':
            count = paddle.zeros(dim_size)
            count.put_along_axis_(axis=0, indices=index, values=paddle.ones(src.shape[dim]))
            count = count.clamp(min=1)

            index = broadcast(index, src, dim)
            out = paddle.zeros(size).put_along_axis_(axis=dim, indices=index, values=src)

            return out / broadcast(count, out, dim)

        # For "min" and "max" reduction, we prefer `scatter_reduce_` on CPU or
        # in case the input does not require gradients:PADDLE
        if reduce in ['min', 'max', 'amin', 'amax']:
            if (not paddle_geometric.typing.WITH_PADDLE_SCATTER
                    or is_compiling() or is_in_onnx_export() or not src.place.is_gpu_place()
                    or not src.requires_grad):

                if hasattr(src, 'requires_grad') and src.requires_grad:
                    print("Warning: Gradient computation is not yet supported in PaddlePaddle for this function.")
                    if (src.place.is_gpu_place() and src.requires_grad and not is_compiling()
                            and not is_in_onnx_export()):
                        warnings.warn(f"The usage of `scatter(reduce='{reduce}')` "
                                      f"can be accelerated via the 'torch-scatter'"
                                      f" package, but it was not found")

                index = broadcast(index, src, dim)
                if not is_in_onnx_export():
                    return paddle.zeros(size).scatter_reduce_(
                        dim, index, src, reduce=f'a{reduce[-3:]}',
                        include_self=False)

                fill = paddle.full(  # type: ignore
                    shape=[1, ],
                    fill_value=src.min() if 'max' in reduce else src.max(),
                    dtype=src.dtype,
                ).expand_as(src)
                out = paddle.zeros(size).scatter_reduce_(
                    dim, index, fill, reduce=f'a{reduce[-3:]}',
                    include_self=True)
                return out.scatter_reduce_(dim, index, src,
                                           reduce=f'a{reduce[-3:]}',
                                           include_self=True)

            return paddle_scatter.scatter(src, index, dim, dim_size=dim_size,
                                         reduce=reduce[-3:])

        # For "mul" reduction, we prefer `scatter_reduce_` on CPU:
        if reduce == 'mul':
            if (not paddle_geometric.typing.WITH_PADDLE_SCATTER
                    or is_compiling() or not src.place.is_gpu_place()):

                if src.place.is_gpu_place() and not is_compiling():
                    warnings.warn(f"The usage of `scatter(reduce='{reduce}')` "
                                  f"can be accelerated via the 'torch-scatter'"
                                  f" package, but it was not found")

                index = broadcast(index, src, dim)
                # We initialize with `one` here to match `scatter_mul` output:
                return paddle.ones(size).scatter_reduce_(
                    dim, index, src, reduce='prod', include_self=True)

            return paddle_scatter.scatter(src, index, dim, dim_size=dim_size,
                                         reduce='mul')

        raise ValueError(f"Encountered invalid `reduce` argument '{reduce}'")

else:  # pragma: no cover
    def scatter(
        src: Tensor,
        index: Tensor,
        dim: int = 0,
        dim_size: Optional[int] = None,
        reduce: str = 'sum',
    ) -> Tensor:
        r"""Reduces all values from the :obj:`src` tensor at the indices
        specified in the :obj:`index` tensor along a given dimension
        :obj:`dim`. See the `documentation
        <https://paddle-scatter.readthedocs.io/en/latest/functions/
        scatter.html>`_ of the :obj:`torch_scatter` package for more
        information.

        Args:
            src (torch.Tensor): The source tensor.
            index (torch.Tensor): The index tensor.
            dim (int, optional): The dimension along which to index.
                (default: :obj:`0`)
            dim_size (int, optional): The size of the output tensor at
                dimension :obj:`dim`. If set to :obj:`None`, will create a
                minimal-sized output tensor according to
                :obj:`index.max() + 1`. (default: :obj:`None`)
            reduce (str, optional): The reduce operation (:obj:`"sum"`,
                :obj:`"mean"`, :obj:`"mul"`, :obj:`"min"` or :obj:`"max"`,
                :obj:`"any"`). (default: :obj:`"sum"`)
        """
        if reduce == 'any':
            dim = src.dim() + dim if dim < 0 else dim

            if dim_size is None:
                dim_size = int(index.max()) + 1 if index.numel() > 0 else 0

            size = src.shape[:dim] + (dim_size, ) + src.shape[dim + 1:]

            index = broadcast(index, src, dim)
            return paddle.zeros(size).scatter_(dim, index, src)

        if not paddle_geometric.typing.WITH_PADDLE_SCATTER:
            raise ImportError("'scatter' requires the 'paddle-scatter' package")

        if reduce == 'amin' or reduce == 'amax':
            reduce = reduce[-3:]

        return paddle_scatter.scatter(src, index, dim, dim_size=dim_size,
                                     reduce=reduce)


def broadcast(src: Tensor, ref: Tensor, dim: int) -> Tensor:
    dim = ref.dim() + dim if dim < 0 else dim
    size = [1] * dim + [-1] + [1] * (ref.dim() - dim - 1)
    return paddle.reshape(src, size).expand_as(ref)


def scatter_argmax(
    src: Tensor,
    index: Tensor,
    dim: int = 0,
    dim_size: Optional[int] = None,
) -> Tensor:

    if (paddle_geometric.typing.WITH_PADDLE_SCATTER and not is_compiling()
            and not is_in_onnx_export()):
        out = paddle_scatter.scatter_max(src, index, dim=dim, dim_size=dim_size)
        return out[1]

    # Only implemented under certain conditions for now :(
    assert src.dim() == 1 and index.dim() == 1
    assert dim == 0 or dim == -1
    assert src.numel() == index.numel()

    if dim_size is None:
        dim_size = int(index.max()) + 1 if index.numel() > 0 else 0

    raise ValueError("'Not Implemented scatter_argmax' requires Paddle")

    out = index.new_full((dim_size, ), fill_value=dim_size - 1)
    nonzero = (src == res[index]).nonzero().view(-1)
    out[index[nonzero]] = nonzero

    return out


def group_argsort(
    src: Tensor,
    index: Tensor,
    dim: int = 0,
    num_groups: Optional[int] = None,
    descending: bool = False,
    return_consecutive: bool = False,
    stable: bool = False,
) -> Tensor:
    r"""Returns the indices that sort the tensor :obj:`src` along a given
    dimension in ascending order by value.
    In contrast to :meth:`torch.argsort`, sorting is performed in groups
    according to the values in :obj:`index`.

    Args:
        src (torch.Tensor): The source tensor.
        index (torch.Tensor): The index tensor.
        dim (int, optional): The dimension along which to index.
            (default: :obj:`0`)
        num_groups (int, optional): The number of groups.
            (default: :obj:`None`)
        descending (bool, optional): Controls the sorting order (ascending or
            descending). (default: :obj:`False`)
        return_consecutive (bool, optional): If set to :obj:`True`, will not
            offset the output to start from :obj:`0` for each group.
            (default: :obj:`False`)
        stable (bool, optional): Controls the relative order of equivalent
            elements. (default: :obj:`False`)

    Example:
        >>> src = torch.tensor([0, 1, 5, 4, 3, 2, 6, 7, 8])
        >>> index = torch.tensor([0, 0, 1, 1, 1, 1, 2, 2, 2])
        >>> group_argsort(src, index)
        tensor([0, 1, 3, 2, 1, 0, 0, 1, 2])
    """
    # Only implemented under certain conditions for now :(
    assert src.dim() == 1 and index.dim() == 1
    assert dim == 0 or dim == -1
    assert src.numel() == index.numel()

    if src.numel() == 0:
        return paddle.zeros_like(src)

    # Normalize `src` to range [0, 1]:
    src = src - src.min()
    src = src / src.max()

    # Compute `grouped_argsort`:
    src = src - 2 * index if descending else src + 2 * index

    perm = src.argsort(descending=descending)
    if stable:
        warnings.warn("Ignoring option `stable=True` in 'group_argsort' "
                      "since it requires Paddle")

    out = paddle.empty_like(index)
    out[perm] = paddle.arange(index.numel(), device=index.device)

    if return_consecutive:
        return out

    # Compute cumulative sum of number of entries with the same index:
    count = scatter(paddle.ones_like(index), index, dim=dim,
                    dim_size=num_groups, reduce='sum')
    ptr = cumsum(count)

    return out - ptr[index]


def group_cat(
    tensors: Union[List[Tensor], Tuple[Tensor, ...]],
    indices: Union[List[Tensor], Tuple[Tensor, ...]],
    dim: int = 0,
    return_index: bool = False,
) -> Union[Tensor, Tuple[Tensor, Tensor]]:
    r"""Concatenates the given sequence of tensors :obj:`tensors` in the given
    dimension :obj:`dim`.
    Different from :meth:`torch.cat`, values along the concatenating dimension
    are grouped according to the indices defined in the :obj:`index` tensors.
    All tensors must have the same shape (except in the concatenating
    dimension).

    Args:
        tensors ([Tensor]): Sequence of tensors.
        indices ([Tensor]): Sequence of index tensors.
        dim (int, optional): The dimension along which the tensors are
            concatenated. (default: :obj:`0`)
        return_index (bool, optional): If set to :obj:`True`, will return the
            new index tensor. (default: :obj:`False`)

    Example:
        >>> x1 = torch.tensor([[0.2716, 0.4233],
        ...                    [0.3166, 0.0142],
        ...                    [0.2371, 0.3839],
        ...                    [0.4100, 0.0012]])
        >>> x2 = torch.tensor([[0.3752, 0.5782],
        ...                    [0.7757, 0.5999]])
        >>> index1 = torch.tensor([0, 0, 1, 2])
        >>> index2 = torch.tensor([0, 2])
        >>> scatter_concat([x1,x2], [index1, index2], dim=0)
        tensor([[0.2716, 0.4233],
                [0.3166, 0.0142],
                [0.3752, 0.5782],
                [0.2371, 0.3839],
                [0.4100, 0.0012],
                [0.7757, 0.5999]])
    """
    assert len(tensors) == len(indices)
    index, perm = paddle.concat(indices).sort(stable=True)
    out = paddle.concat(tensors, axis=0)[perm]
    return (out, index) if return_index else out
