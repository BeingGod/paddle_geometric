import os.path as osp
from typing import Callable, List, Optional

import paddle
from paddle_geometric.data import Data, InMemoryDataset, download_url
from paddle_geometric.utils import coalesce


class Airports(InMemoryDataset):
    r"""The Airports dataset from the `"struc2vec: Learning Node
    Representations from Structural Identity"
    <https://arxiv.org/abs/1704.03165>`_ paper, where nodes denote airports
    and labels correspond to activity levels.
    Features are given by one-hot encoded node identifiers, as described in the
    `"GraLSP: Graph Neural Networks with Local Structural Patterns"
    <https://arxiv.org/abs/1911.07675>`_ paper.

    Args:
        root: Root directory where the dataset should be saved.
        name: The name of the dataset (:obj:`"USA"`, :obj:`"Brazil"`,
            :obj:`"Europe"`).
        transform: A function/transform that takes in an
            :class:`paddle_geometric.data.Data` object and returns a transformed
            version. The data object will be transformed before every access.
        pre_transform (callable, optional): A function/transform that takes in
            :class:`paddle_geometric.data.Data` object and returns a
            transformed version. The data object will be transformed before
            being saved to disk.
        force_reload: Whether to re-process the dataset.
    """
    edge_url = ('https://github.com/leoribeiro/struc2vec/'
                'raw/master/graph/{}-airports.edgelist')
    label_url = ('https://github.com/leoribeiro/struc2vec/'
                 'raw/master/graph/labels-{}-airports.txt')

    def __init__(
        self,
        root: str,
        name: str,
        transform: Optional[Callable] = None,
        pre_transform: Optional[Callable] = None,
        force_reload: bool = False,
    ) -> None:
        self.name = name.lower()
        assert self.name in ['usa', 'brazil', 'europe']
        super().__init__(root, transform, pre_transform,
                         force_reload=force_reload)
        self.load(self.processed_paths[0])

    @property
    def raw_dir(self) -> str:
        return osp.join(self.root, self.name, 'raw')

    @property
    def processed_dir(self) -> str:
        return osp.join(self.root, self.name, 'processed')

    @property
    def raw_file_names(self) -> List[str]:
        return [
            f'{self.name}-airports.edgelist',
            f'labels-{self.name}-airports.txt',
        ]

    @property
    def processed_file_names(self) -> str:
        return 'data.pt'

    def download(self) -> None:
        download_url(self.edge_url.format(self.name), self.raw_dir)
        download_url(self.label_url.format(self.name), self.raw_dir)

    def process(self) -> None:
        index_map, ys = {}, []
        with open(self.raw_paths[1]) as f:
            rows = f.read().split('\n')[1:-1]
            for i, row in enumerate(rows):
                idx, label = row.split()
                index_map[int(idx)] = i
                ys.append(int(label))
        y = paddle.to_tensor(ys, dtype='int64')
        x = paddle.eye(y.shape[0], dtype='float32')

        edge_indices = []
        with open(self.raw_paths[0]) as f:
            rows = f.read().split('\n')[:-1]
            for row in rows:
                src, dst = row.split()
                edge_indices.append([index_map[int(src)], index_map[int(dst)]])
        edge_index = paddle.to_tensor(edge_indices, dtype='int64').transpose([1, 0])
        edge_index = coalesce(edge_index, num_nodes=y.shape[0])

        data = Data(x=x, edge_index=edge_index, y=y)
        data = data if self.pre_transform is None else self.pre_transform(data)
        self.save([data], self.processed_paths[0])

    def __repr__(self) -> str:
        return f'{self.name.capitalize()}Airports()'
