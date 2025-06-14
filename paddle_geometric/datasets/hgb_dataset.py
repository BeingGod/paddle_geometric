import json
import os
import os.path as osp
from collections import defaultdict
from typing import Callable, Dict, List, Optional

import paddle

from paddle_geometric.data import HeteroData, InMemoryDataset, download_url, extract_zip


class HGBDataset(InMemoryDataset):
    r"""A variety of heterogeneous graph benchmark datasets from the
    `"Are We Really Making Much Progress? Revisiting, Benchmarking, and
    Refining Heterogeneous Graph Neural Networks"
    <http://keg.cs.tsinghua.edu.cn/jietang/publications/
    KDD21-Lv-et-al-HeterGNN.pdf>`_ paper.

    .. note::
        Test labels are randomly given to prevent data leakage issues.
        If you want to obtain final test performance, you will need to submit
        your model predictions to the
        `HGB leaderboard <https://www.biendata.xyz/hgb/>`_.

    Args:
        root (str): Root directory where the dataset should be saved.
        name (str): The name of the dataset (one of :obj:`"ACM"`,
            :obj:`"DBLP"`, :obj:`"Freebase"`, :obj:`"IMDB"`)
        transform (callable, optional): A function/transform that takes in an
            :class:`paddle_geometric.data.HeteroData` object and returns a
            transformed version. The data object will be transformed before
            every access. (default: :obj:`None`)
        pre_transform (callable, optional): A function/transform that takes in
            an :class:`paddle_geometric.data.HeteroData` object and returns a
            transformed version. The data object will be transformed before
            being saved to disk. (default: :obj:`None`)
        force_reload (bool, optional): Whether to re-process the dataset.
            (default: :obj:`False`)
    """
    names = {
        'acm': 'ACM',
        'dblp': 'DBLP',
        'freebase': 'Freebase',
        'imdb': 'IMDB',
    }

    file_ids = {
        'acm': '1xbJ4QE9pcDJOcALv7dYhHDCPITX2Iddz',
        'dblp': '1fLLoy559V7jJaQ_9mQEsC06VKd6Qd3SC',
        'freebase': '1vw-uqbroJZfFsWpriC1CWbtHCJMGdWJ7',
        'imdb': '18qXmmwKJBrEJxVQaYwKTL3Ny3fPqJeJ2',
    }

    def __init__(
        self,
        root: str,
        name: str,
        transform: Optional[Callable] = None,
        pre_transform: Optional[Callable] = None,
        force_reload: bool = False,
    ) -> None:
        self.name = name.lower()
        assert self.name in set(self.names.keys())
        super().__init__(root, transform, pre_transform,
                         force_reload=force_reload)
        self.load(self.processed_paths[0], data_cls=HeteroData)

    @property
    def raw_dir(self) -> str:
        return osp.join(self.root, self.name, 'raw')

    @property
    def processed_dir(self) -> str:
        return osp.join(self.root, self.name, 'processed')

    @property
    def raw_file_names(self) -> List[str]:
        x = ['info.dat', 'node.dat', 'link.dat', 'label.dat', 'label.dat.test']
        return [osp.join(self.names[self.name], f) for f in x]

    @property
    def processed_file_names(self) -> str:
        return 'data.pdparams'

    def download(self) -> None:
        id = self.file_ids[self.name]
        path = download_url(f'https://drive.google.com/uc?id={id}', self.raw_dir, 'data.zip')
        extract_zip(path, self.raw_dir)
        os.unlink(path)

    def process(self) -> None:
        data = HeteroData()

        # node_types = {0: 'paper', 1, 'author', ...}
        # edge_types = {0: ('paper', 'cite', 'paper'), ...}
        if self.name in ['acm', 'dblp', 'imdb']:
            with open(self.raw_paths[0]) as f:  # `info.dat`
                info = json.load(f)
            n_types = info['node.dat']['node type']
            n_types = {int(k): v for k, v in n_types.items()}
            e_types = info['link.dat']['link type']
            e_types = {int(k): tuple(v.values()) for k, v in e_types.items()}
            for key, (src, dst, rel) in e_types.items():
                src, dst = n_types[int(src)], n_types[int(dst)]
                rel = rel.split('-')[1]
                rel = rel if rel != dst and rel[1:] != dst else 'to'
                e_types[key] = (src, rel, dst)
            num_classes = len(info['label.dat']['node type']['0'])

        # Extract node information:
        mapping_dict = {}
        x_dict = defaultdict(list)
        num_nodes_dict: Dict[str, int] = defaultdict(int)
        with open(self.raw_paths[1]) as f:  # `node.dat`
            xs = [v.split('\t') for v in f.read().split('\n')[:-1]]
        for x in xs:
            n_id, n_type = int(x[0]), n_types[int(x[2])]
            mapping_dict[n_id] = num_nodes_dict[n_type]
            num_nodes_dict[n_type] += 1
            if len(x) >= 4:
                x_dict[n_type].append([float(v) for v in x[3].split(',')])
        for n_type in n_types.values():
            if len(x_dict[n_type]) == 0:
                data[n_type].num_nodes = num_nodes_dict[n_type]
            else:
                data[n_type].x = paddle.to_tensor(x_dict[n_type], dtype='float32')

        edge_index_dict = defaultdict(list)
        edge_weight_dict = defaultdict(list)
        with open(self.raw_paths[2]) as f:  # `link.dat`
            edges = [v.split('\t') for v in f.read().split('\n')[:-1]]
        for src, dst, rel, weight in edges:
            e_type = e_types[int(rel)]
            src, dst = mapping_dict[int(src)], mapping_dict[int(dst)]
            edge_index_dict[e_type].append([src, dst])
            edge_weight_dict[e_type].append(float(weight))
        for e_type in e_types.values():
            edge_index = paddle.to_tensor(edge_index_dict[e_type], dtype='int64').T
            edge_weight = paddle.to_tensor(edge_weight_dict[e_type], dtype='float32')
            data[e_type].edge_index = edge_index
            if not paddle.allclose(edge_weight, paddle.ones_like(edge_weight)):
                data[e_type].edge_weight = edge_weight

        # Node classification:
        with open(self.raw_paths[3]) as f:
            train_ys = [v.split('\t') for v in f.read().split('\n')[:-1]]
        with open(self.raw_paths[4]) as f:
            test_ys = [v.split('\t') for v in f.read().split('\n')[:-1]]
        for y in train_ys:
            n_id, n_type = mapping_dict[int(y[0])], n_types[int(y[2])]
            if not hasattr(data[n_type], 'y'):
                num_nodes = data[n_type].num_nodes
                data[n_type].y = paddle.full([num_nodes], -1, dtype='int64')
                data[n_type].train_mask = paddle.zeros([num_nodes], dtype='bool')
                data[n_type].test_mask = paddle.zeros([num_nodes], dtype='bool')
            data[n_type].y[int(n_id)] = int(y[3])
            data[n_type].train_mask[int(n_id)] = True
        for y in test_ys:
            n_id, n_type = mapping_dict[int(y[0])], n_types[int(y[2])]
            data[n_type].y[int(n_id)] = int(y[3])
            data[n_type].test_mask[int(n_id)] = True

        if self.pre_transform is not None:
            data = self.pre_transform(data)

        self.save([data], self.processed_paths[0])

    def __repr__(self) -> str:
        return f'{self.names[self.name]}()'
