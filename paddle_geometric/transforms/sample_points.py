import paddle

from paddle_geometric.data import Data
from paddle_geometric.data.datapipes import functional_transform
from paddle_geometric.transforms import BaseTransform


@functional_transform('sample_points')
class SamplePoints(BaseTransform):
    r"""Uniformly samples a fixed number of points on the mesh faces according
    to their face area (functional name: :obj:`sample_points`).

    Args:
        num (int): The number of points to sample.
        remove_faces (bool, optional): If set to :obj:`False`, the face tensor
            will not be removed. (default: :obj:`True`)
        include_normals (bool, optional): If set to :obj:`True`, then compute
            normals for each sampled point. (default: :obj:`False`)
    """
    def __init__(
        self,
        num: int,
        remove_faces: bool = True,
        include_normals: bool = False,
    ):
        self.num = num
        self.remove_faces = remove_faces
        self.include_normals = include_normals

    def forward(self, data: Data) -> Data:
        assert data.pos is not None
        assert data.face is not None

        pos, face = data.pos, data.face
        assert pos.shape[1] == 3 and face.shape[0] == 3

        pos_max = paddle.abs(pos).max()
        pos = pos / pos_max

        area = paddle.cross(
            pos[face[1]] - pos[face[0]],
            pos[face[2]] - pos[face[0]],
            axis=1,
        )
        area = paddle.norm(area, p=2, axis=1).abs() / 2

        prob = area / area.sum()
        sample = paddle.multinomial(prob, self.num, replacement=True)
        face = face[:, sample]

        frac = paddle.rand([self.num, 2])
        mask = frac.sum(axis=-1) > 1
        frac[mask] = 1 - frac[mask]

        vec1 = pos[face[1]] - pos[face[0]]
        vec2 = pos[face[2]] - pos[face[0]]

        if self.include_normals:
            data.normal = paddle.nn.functional.normalize(
                paddle.cross(vec1, vec2, axis=1), p=2)

        pos_sampled = pos[face[0]]
        pos_sampled += frac[:, :1] * vec1
        pos_sampled += frac[:, 1:] * vec2

        pos_sampled = pos_sampled * pos_max
        data.pos = pos_sampled

        if self.remove_faces:
            data.face = None

        return data

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}({self.num})'
