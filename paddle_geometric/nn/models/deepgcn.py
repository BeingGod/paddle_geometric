import copy
from typing import Optional

import paddle
from paddle import Tensor
from paddle.nn import Layer
from paddle.nn import functional as F
from paddle.callbacks import ModelCheckpoint


class DeepGCNLayer(paddle.nn.Layer):
    r"""The skip connection operations from the
    `"DeepGCNs: Can GCNs Go as Deep as CNNs?"
    <https://arxiv.org/abs/1904.03751>`_ and `"All You Need to Train Deeper
    GCNs" <https://arxiv.org/abs/2006.07739>`_ papers.
    The implemented skip connections includes the pre-activation residual
    connection (:obj:`"res+"`), the residual connection (:obj:`"res"`),
    the dense connection (:obj:`"dense"`) and no connections (:obj:`"plain"`).

    * **Res+** (:obj:`"res+"`):

    .. math::
        \text{Normalization}\to\text{Activation}\to\text{Dropout}\to
        \text{GraphConv}\to\text{Res}

    * **Res** (:obj:`"res"`) / **Dense** (:obj:`"dense"`) / **Plain**
      (:obj:`"plain"`):

    .. math::
        \text{GraphConv}\to\text{Normalization}\to\text{Activation}\to
        \text{Res/Dense/Plain}\to\text{Dropout}

    Args:
        conv (paddle.nn.Layer, optional): the GCN operator.
            (default: :obj:`None`)
        norm (paddle.nn.Layer): the normalization layer. (default: :obj:`None`)
        act (paddle.nn.Layer): the activation layer. (default: :obj:`None`)
        block (str, optional): The skip connection operation to use
            (:obj:`"res+"`, :obj:`"res"`, :obj:`"dense"` or :obj:`"plain"`).
            (default: :obj:`"res+"`)
        dropout (float, optional): Whether to apply or dropout.
            (default: :obj:`0.`)
        ckpt_grad (bool, optional): If set to :obj:`True`, will checkpoint this
            part of the model. Checkpointing works by trading compute for
            memory, since intermediate activations do not need to be kept in
            memory. Set this to :obj:`True` in case you encounter out-of-memory
            errors while going deep. (default: :obj:`False`)
    """

    def __init__(
        self,
        conv: Optional[Layer] = None,
        norm: Optional[Layer] = None,
        act: Optional[Layer] = None,
        block: str = 'res+',
        dropout: float = 0.,
        ckpt_grad: bool = False,
    ):
        super().__init__()

        self.conv = conv
        self.norm = norm
        self.act = act
        self.block = block.lower()
        assert self.block in ['res+', 'res', 'dense', 'plain']
        self.dropout = dropout
        self.ckpt_grad = ckpt_grad

    def reset_parameters(self):
        r"""Resets all learnable parameters of the module."""
        self.conv.reset_parameters()
        self.norm.reset_parameters()

    def forward(self, *args, **kwargs) -> Tensor:
        """"""  # noqa: D419
        args = list(args)
        x = args.pop(0)

        if self.block == 'res+':
            h = x
            if self.norm is not None:
                h = self.norm(h)
            if self.act is not None:
                h = self.act(h)
            h = F.dropout(h, p=self.dropout, training=self.training)
            if self.conv is not None and self.ckpt_grad and h.stop_gradient:
                h = ModelCheckpoint(self.conv, h, *args, use_reentrant=True,
                               **kwargs)
            else:
                h = self.conv(h, *args, **kwargs)

            return x + h

        else:
            if self.conv is not None and self.ckpt_grad and x.stop_gradient:
                h = ModelCheckpoint(self.conv, x, *args, use_reentrant=True,
                               **kwargs)
            else:
                h = self.conv(x, *args, **kwargs)
            if self.norm is not None:
                h = self.norm(h)
            if self.act is not None:
                h = self.act(h)

            if self.block == 'res':
                h = x + h
            elif self.block == 'dense':
                h = paddle.concat([x, h], axis=-1)
            elif self.block == 'plain':
                pass

            return F.dropout(h, p=self.dropout, training=self.training)

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}(block={self.block})'
