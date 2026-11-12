# neg-cache-depth-general-warm

先预热子树 sub（41 篇），再经 c0..c30 引用它：第 31 层命中缓存，累计深度越界。深度取首个越界层（65），且引用链必须**逐层**展开到 s31.pxl——只写 sub.pxl -> s31.pxl 是把捷径当成期望（发布态复核 P2）。
