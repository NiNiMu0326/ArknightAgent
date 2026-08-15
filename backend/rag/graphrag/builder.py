import json
import logging
import networkx as nx
from typing import Dict, List
from pathlib import Path

logger = logging.getLogger(__name__)


class GraphBuilder:
    def __init__(self, entity_relations_path: str = None):
        if entity_relations_path:
            self.entity_relations_path = entity_relations_path
        else:
            from backend.config import ENTITY_RELATIONS_FILE
            self.entity_relations_path = str(ENTITY_RELATIONS_FILE)
        self.graph = None

    def build(self, force: bool = False) -> nx.DiGraph:
        """Build NetworkX directed graph from entity relations.
        
        Uses DiGraph (directed) to preserve relationship directionality.
        Path finding treats the graph as undirected to discover all connections.
        """
        if self.graph is not None and not force:
            return self.graph

        self.graph = nx.DiGraph()

        if not Path(self.entity_relations_path).exists():
            raise FileNotFoundError(
                f"Entity relations file not found: {self.entity_relations_path}"
            )

        with open(self.entity_relations_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # entities 格式: {"干员": [...], "组织": [...], ...} 或 [{"entity": "银灰", "type": "干员"}, ...]
        entities_data = data.get('entities', [])

        # Add nodes (entities)
        if isinstance(entities_data, dict):
            # 新格式: 按类型分组的 dict
            for entity_type, names in entities_data.items():
                if isinstance(names, list):
                    for name in names:
                        if isinstance(name, str) and name:
                            self.graph.add_node(name, type=entity_type)
        elif isinstance(entities_data, list):
            # 旧格式: 列表
            for e in entities_data:
                if isinstance(e, dict):
                    self.graph.add_node(e.get('entity', ''), type=e.get('type', '干员'))

        # Add edges (relations)
        for relation in data.get('relations', []):
            self.graph.add_edge(
                relation['source'],
                relation['target'],
                relation=relation.get('relation', ''),
                description=relation.get('description', '')
            )

        print(f"Built graph: {self.graph.number_of_nodes()} nodes, {self.graph.number_of_edges()} edges")
        return self.graph

    def get_neighbors(self, entity: str, depth: int = 1) -> List[Dict]:
        """Get neighboring entities and their relations (both directions in directed graph)."""
        if self.graph is None:
            self.build()

        if entity not in self.graph:
            return []

        neighbors = []
        seen = set()
        
        # Outgoing edges: entity -> neighbor
        for neighbor in self.graph.successors(entity):
            if neighbor in seen:
                continue
            seen.add(neighbor)
            edge_data = self.graph[entity][neighbor]
            neighbors.append({
                'entity': neighbor,
                'direction': 'outgoing',
                'relation': edge_data.get('relation', ''),
                'description': edge_data.get('description', '')
            })
        
        # Incoming edges: neighbor -> entity
        for neighbor in self.graph.predecessors(entity):
            if neighbor in seen:
                continue
            seen.add(neighbor)
            edge_data = self.graph[neighbor][entity]
            neighbors.append({
                'entity': neighbor,
                'direction': 'incoming',
                'relation': edge_data.get('relation', ''),
                'description': edge_data.get('description', '')
            })

        return neighbors

    def get_all_relations(self, entity: str) -> Dict:
        """Get all relations for an entity in a directed graph."""
        if self.graph is None:
            self.build()

        if entity not in self.graph:
            return {'incoming': [], 'outgoing': []}

        incoming = []
        outgoing = []

        # Outgoing: entity -> successor
        for neighbor in self.graph.successors(entity):
            edge_data = self.graph[entity][neighbor]
            outgoing.append({
                'entity': neighbor,
                'relation': edge_data.get('relation', ''),
                'description': edge_data.get('description', '')
            })

        # Incoming: predecessor -> entity
        for neighbor in self.graph.predecessors(entity):
            edge_data = self.graph[neighbor][entity]
            incoming.append({
                'entity': neighbor,
                'relation': edge_data.get('relation', ''),
                'description': edge_data.get('description', '')
            })

        return {'incoming': incoming, 'outgoing': outgoing}

    def find_path(self, entity1: str, entity2: str, max_hops: int = 4) -> Dict:
        """Find shortest path between two entities with edge details.

        Path finding treats the graph as undirected (to discover all connections),
        but edge information is extracted from the directed graph with correct directionality.

        Args:
            entity1: First entity name
            entity2: Second entity name
            max_hops: Maximum number of hops (edges) allowed. Default 4.
                     Paths longer than this are considered meaningless.

        Returns:
            Dict with 'path' (list of entity names) and 'edges' (list of edge details).
            Empty dict values if no path found or path exceeds max_hops.
        """
        if self.graph is None:
            self.build()

        if entity1 == entity2:
            if entity1 in self.graph:
                return {"path": [entity1], "edges": []}
            return {"path": [], "edges": []}

        try:
            # Use undirected view for path finding (discover all connections)
            undirected = self.graph.to_undirected()
            # Use single_source_shortest_path with cutoff to limit path length
            paths = nx.single_source_shortest_path(undirected, entity1, cutoff=max_hops)
            if entity2 not in paths:
                return {"path": [], "edges": []}
            path = paths[entity2]
            
            # Extract edge details from the directed graph
            edges = []
            for i in range(len(path) - 1):
                src, tgt = path[i], path[i + 1]
                edge_data = {}
                direction = "unknown"
                
                # Try forward edge first
                if self.graph.has_edge(src, tgt):
                    edge_data = dict(self.graph[src][tgt])
                    direction = "forward"
                # Try reverse edge
                elif self.graph.has_edge(tgt, src):
                    edge_data = dict(self.graph[tgt][src])
                    direction = "reverse"
                    # Swap to show actual edge direction
                    src, tgt = tgt, src
                
                edges.append({
                    "from": src,
                    "to": tgt,
                    "relation": edge_data.get('relation', ''),
                    "description": edge_data.get('description', ''),
                    "direction": direction,
                })
            
            return {"path": path, "edges": edges}
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return {"path": [], "edges": []}

    def save(self, path: str):
        """Save graph to file."""
        if self.graph is None:
            return
        nx.write_gml(self.graph, path)
        print(f"Saved graph to {path}")

    @classmethod
    def load(cls, path: str) -> 'GraphBuilder':
        """Load graph from file."""
        builder = cls()
        builder.graph = nx.read_gml(path)
        return builder
