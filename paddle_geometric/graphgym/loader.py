import os.path as osp
from typing import Callable

import paddle

import paddle_geometric.graphgym.register as register
import paddle_geometric.transforms as T
from paddle_geometric.datasets import (
    PPI,
    Amazon,
    Coauthor,
    KarateClub,
    MNISTSuperpixels,
    Planetoid,
    QM7b,
    TUDataset,
)
from paddle_geometric.graphgym.config import cfg
from paddle_geometric.graphgym.models.transform import (
    create_link_label,
    neg_sampling_transform,
)
from paddle_geometric.loader import (
    ClusterLoader,
    DataLoader,
    GraphSAINTEdgeSampler,
    GraphSAINTNodeSampler,
    GraphSAINTRandomWalkSampler,
    NeighborSampler,
    RandomNodeLoader,
)
from paddle_geometric.utils import (
    index_to_mask,
    negative_sampling,
    to_undirected,
)

index2mask = index_to_mask  # TODO: Backward compatibility


def planetoid_dataset(name: str) -> Callable:
    return lambda root: Planetoid(root, name)


register.register_dataset('Cora', planetoid_dataset('Cora'))
register.register_dataset('CiteSeer', planetoid_dataset('CiteSeer'))
register.register_dataset('PubMed', planetoid_dataset('PubMed'))
register.register_dataset('PPI', PPI)


def load_pyg(name, dataset_dir):
    """Load PaddleGeometric dataset objects. (More datasets will be supported).

    Args:
        name (str): dataset name
        dataset_dir (str): data directory

    Returns: PaddleGeometric dataset object
    """
    dataset_dir = osp.join(dataset_dir, name)
    if name in ['Cora', 'CiteSeer', 'PubMed']:
        dataset = Planetoid(dataset_dir, name)
    elif name[:3] == 'TU_':
        if name[3:] == 'IMDB':
            name = 'IMDB-MULTI'
            dataset = TUDataset(dataset_dir, name, transform=T.Constant())
        else:
            dataset = TUDataset(dataset_dir, name[3:])
    elif name == 'Karate':
        dataset = KarateClub()
    elif 'Coauthor' in name:
        dataset = Coauthor(dataset_dir, name='CS' if 'CS' in name else 'Physics')
    elif 'Amazon' in name:
        dataset = Amazon(dataset_dir, name='Computers' if 'Computers' in name else 'Photo')
    elif name == 'MNIST':
        dataset = MNISTSuperpixels(dataset_dir)
    elif name == 'PPI':
        dataset = PPI(dataset_dir)
    elif name == 'QM7b':
        dataset = QM7b(dataset_dir)
    else:
        raise ValueError(f"'{name}' not supported")

    return dataset


def set_dataset_attr(dataset, name, value, size):
    dataset._data_list = None
    dataset.data[name] = value
    if dataset.slices is not None:
        dataset.slices[name] = paddle.to_tensor([0, size], dtype='int64')


def load_ogb(name, dataset_dir):
    """Load OGB dataset objects.

    Args:
        name (str): dataset name
        dataset_dir (str): data directory

    Returns: PaddleGeometric dataset object
    """
    from ogb.graphproppred import PygGraphPropPredDataset
    from ogb.linkproppred import PygLinkPropPredDataset
    from ogb.nodeproppred import PygNodePropPredDataset

    if name[:4] == 'ogbn':
        dataset = PygNodePropPredDataset(name=name, root=dataset_dir)
        splits = dataset.get_idx_split()
        split_names = ['train_mask', 'val_mask', 'test_mask']
        for i, key in enumerate(splits.keys()):
            mask = index_to_mask(splits[key], size=dataset._data.y.shape[0])
            set_dataset_attr(dataset, split_names[i], mask, len(mask))
        edge_index = to_undirected(dataset._data.edge_index)
        set_dataset_attr(dataset, 'edge_index', edge_index, edge_index.shape[1])

    elif name[:4] == 'ogbg':
        dataset = PygGraphPropPredDataset(name=name, root=dataset_dir)
        splits = dataset.get_idx_split()
        split_names = ['train_graph_index', 'val_graph_index', 'test_graph_index']
        for i, key in enumerate(splits.keys()):
            id = splits[key]
            set_dataset_attr(dataset, split_names[i], id, len(id))

    elif name[:4] == "ogbl":
        dataset = PygLinkPropPredDataset(name=name, root=dataset_dir)
        splits = dataset.get_edge_split()
        id = splits['train']['edge'].T
        if cfg.dataset.resample_negative:
            set_dataset_attr(dataset, 'train_pos_edge_index', id, id.shape[1])
            dataset.transform = neg_sampling_transform
        else:
            id_neg = negative_sampling(edge_index=id, num_nodes=dataset._data.num_nodes, num_neg_samples=id.shape[1])
            id_all = paddle.concat([id, id_neg], axis=-1)
            label = create_link_label(id, id_neg)
            set_dataset_attr(dataset, 'train_edge_index', id_all, id_all.shape[1])
            set_dataset_attr(dataset, 'train_edge_label', label, len(label))

        id, id_neg = splits['valid']['edge'].T, splits['valid']['edge_neg'].T
        id_all = paddle.concat([id, id_neg], axis=-1)
        label = create_link_label(id, id_neg)
        set_dataset_attr(dataset, 'val_edge_index', id_all, id_all.shape[1])
        set_dataset_attr(dataset, 'val_edge_label', label, len(label))

        id, id_neg = splits['test']['edge'].T, splits['test']['edge_neg'].T
        id_all = paddle.concat([id, id_neg], axis=-1)
        label = create_link_label(id, id_neg)
        set_dataset_attr(dataset, 'test_edge_index', id_all, id_all.shape[1])
        set_dataset_attr(dataset, 'test_edge_label', label, len(label))

    else:
        raise ValueError(f'OGB dataset: {name} does not exist')
    return dataset


def load_dataset():
    """Load dataset objects.

    Returns: PaddleGeometric dataset object
    """
    format = cfg.dataset.format
    name = cfg.dataset.name
    dataset_dir = cfg.dataset.dir
    for func in register.loader_dict.values():
        dataset = func(format, name, dataset_dir)
        if dataset is not None:
            return dataset
    if format == 'PyG':
        dataset = load_pyg(name, dataset_dir)
    elif format == 'OGB':
        dataset = load_ogb(name.replace('_', '-'), dataset_dir)
    else:
        raise ValueError(f"Unknown data format '{format}'")
    return dataset


def set_dataset_info(dataset):
    """Set global dataset information.

    Args:
        dataset: PaddleGeometric dataset object
    """
    try:
        cfg.share.dim_in = dataset._data.x.shape[1]
    except Exception:
        cfg.share.dim_in = 1
    try:
        cfg.share.dim_out = paddle.unique(dataset._data.y).shape[0] if cfg.dataset.task_type == 'classification' else dataset._data.y.shape[1]
    except Exception:
        cfg.share.dim_out = 1
    cfg.share.num_splits = 1
    if any('val' in key for key in dataset._data.keys()):
        cfg.share.num_splits += 1
    if any('test' in key for key in dataset._data.keys()):
        cfg.share.num_splits += 1


def create_dataset():
    """Create dataset object.

    Returns: PaddleGeometric dataset object
    """
    dataset = load_dataset()
    set_dataset_info(dataset)
    return dataset


def get_loader(dataset, sampler, batch_size, shuffle=True):
    """Get loader based on the sampler type."""
    pw = cfg.num_workers > 0
    if sampler == "full_batch" or len(dataset) > 1:
        loader_train = DataLoader(
            dataset,
            batch_size=batch_size,
            shuffle=shuffle,
            num_workers=cfg.num_workers,
            pin_memory=True,
            persistent_workers=pw
        )
    elif sampler == "neighbor":
        loader_train = NeighborSampler(
            dataset[0],
            sizes=cfg.train.neighbor_sizes[:cfg.gnn.layers_mp],
            batch_size=batch_size,
            shuffle=shuffle,
            num_workers=cfg.num_workers,
            pin_memory=True
        )
    elif sampler == "random_node":
        loader_train = RandomNodeLoader(
            dataset[0],
            num_parts=cfg.train.train_parts,
            shuffle=shuffle,
            num_workers=cfg.num_workers,
            pin_memory=True,
            persistent_workers=pw
        )
    elif sampler == "saint_rw":
        loader_train = GraphSAINTRandomWalkSampler(
            dataset[0],
            batch_size=batch_size,
            walk_length=cfg.train.walk_length,
            num_steps=cfg.train.iter_per_epoch,
            sample_coverage=0,
            shuffle=shuffle,
            num_workers=cfg.num_workers,
            pin_memory=True,
            persistent_workers=pw
        )
    elif sampler == "saint_node":
        loader_train = GraphSAINTNodeSampler(
            dataset[0],
            batch_size=batch_size,
            num_steps=cfg.train.iter_per_epoch,
            sample_coverage=0,
            shuffle=shuffle,
            num_workers=cfg.num_workers,
            pin_memory=True,
            persistent_workers=pw
        )
    elif sampler == "saint_edge":
        loader_train = GraphSAINTEdgeSampler(
            dataset[0],
            batch_size=batch_size,
            num_steps=cfg.train.iter_per_epoch,
            sample_coverage=0,
            shuffle=shuffle,
            num_workers=cfg.num_workers,
            pin_memory=True,
            persistent_workers=pw
        )
    elif sampler == "cluster":
        loader_train = ClusterLoader(
            dataset[0],
            num_parts=cfg.train.train_parts,
            save_dir=osp.join(cfg.dataset.dir, cfg.dataset.name.replace("-", "_")),
            batch_size=batch_size,
            shuffle=shuffle,
            num_workers=cfg.num_workers,
            pin_memory=True,
            persistent_workers=pw
        )
    else:
        raise NotImplementedError(f"'{sampler}' is not implemented")

    return loader_train


def create_loader():
    """Create data loader object.

    Returns: List of Paddle data loaders
    """
    dataset = create_dataset()
    if cfg.dataset.task == 'graph':
        id = dataset.data['train_graph_index']
        loaders = [
            get_loader(dataset[id], cfg.train.sampler, cfg.train.batch_size, shuffle=True)
        ]
        delattr(dataset.data, 'train_graph_index')
    else:
        loaders = [
            get_loader(dataset, cfg.train.sampler, cfg.train.batch_size, shuffle=True)
        ]

    # val and test loaders
    for i in range(cfg.share.num_splits - 1):
        if cfg.dataset.task == 'graph':
            split_names = ['val_graph_index', 'test_graph_index']
            id = dataset.data[split_names[i]]
            loaders.append(
                get_loader(dataset[id], cfg.val.sampler, cfg.train.batch_size, shuffle=False)
            )
            delattr(dataset.data, split_names[i])
        else:
            loaders.append(
                get_loader(dataset, cfg.val.sampler, cfg.train.batch_size, shuffle=False)
            )

    return loaders