/** 把静态图层烘焙成纹理。
 *
 * 为什么：Phaser 的 Graphics 不是缓存纹理。每一帧它都会遍历 commandBuffer，
 * 把每条 fillRect 重新三角化成 quad、重新上传。地形一次性画进去 51 万条指令，
 * 迷雾 24 万条——每帧 77 万 quad，而可见区域只占世界的 12.5%。
 *
 * 实测（`docs/VIEW_CULLING_RESEARCH.md`）：拖动时主线程一半以上时间花在
 * `GraphicsWebGLRenderer` 与 `batchQuad` 上。
 *
 * 烘焙之后，静态内容变成一个 quad。代价是内容变化时要重烘一次——而地形只在
 * 扩建后变，迷雾只在揭示半径变时变，都不是每帧的事。
 */

import Phaser from 'phaser';

export interface BakedLayer {
  readonly image: Phaser.GameObjects.Image;
  /** 重烘：内容变了就调它。 */
  readonly rebake: () => void;
  readonly destroy: () => void;
}

/**
 * 用 `draw` 画一遍，把结果存成纹理并贴到 `depth`。
 *
 * `key` 必须是全局唯一的纹理名：同名会复用旧纹理，画面就停在上一版。
 */
export function bakeLayer(
  scene: Phaser.Scene,
  key: string,
  width: number,
  height: number,
  depth: number,
  draw: (g: Phaser.GameObjects.Graphics) => void,
): BakedLayer {
  let image: Phaser.GameObjects.Image | null = null;

  const rebake = (): void => {
    // 先画到一个不进场景的 Graphics 上。
    const g = scene.make.graphics({}, false);
    draw(g);
    // 旧纹理要先删：同 key 的 generateTexture 会直接复用，画面不更新。
    if (scene.textures.exists(key)) scene.textures.remove(key);
    g.generateTexture(key, width, height);
    g.destroy();

    if (image === null) {
      image = scene.add.image(0, 0, key).setOrigin(0, 0);
      image.setDepth(depth);
    } else {
      image.setTexture(key);
    }
  };

  rebake();

  return {
    get image(): Phaser.GameObjects.Image {
      if (image === null) throw new Error('烘焙失败：纹理没有创建');
      return image;
    },
    rebake,
    destroy() {
      image?.destroy();
      image = null;
      if (scene.textures.exists(key)) scene.textures.remove(key);
    },
  };
}
