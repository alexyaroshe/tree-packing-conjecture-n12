# Packing on three vertices

[trees.txt](trees.txt) is the complete tree catalog through order 3. Its only sequence, index 0, consists of an edge $T_2$ and a two-edge path $T_3$.

[packing.cert](packing.cert) contains three binary bytes, hexadecimal 00 01 02, with no header. Host edges are numbered by endpoint pairs: (0,1), (0,2), (1,2). The first two bytes describe $T_3$; the last describes $T_2$.

| Byte | Host edge | Tree |
|---|---|---|
| 00 | (0,1) | $T_3$ |
| 01 | (0,2) | $T_3$ |
| 02 | (1,2) | $T_2$ |

Thus $T_3$ is the path 1–0–2, and $T_2$ is the edge (1,2). The copies share vertices, but each edge of $K_3$ belongs to exactly one tree.
