/** 相对(逆)深度图：值越大越近（Depth Anything 系输出约定） */
export interface DepthResult {
  width: number;
  height: number;
  /** 长度 width*height，行主序 */
  data: Float32Array;
  device: 'webgpu' | 'wasm';
  ms: number;
}

export interface CameraSpec {
  /** 垂直 FOV（度） */
  vfovDeg: number;
  /** 相机在房间坐标系中的位置（米），y 向上 */
  pos: [number, number, number];
  /** 相机朝向：世界系下的相机基（右、下、前），行主序 3x3 */
  basis: number[];
  /** 输入图宽高比 w/h */
  aspect: number;
}

export interface RoomSpec {
  /** 房间坐标系下的包围盒 */
  min: [number, number, number];
  max: [number, number, number];
  /** 哪些侧面判定为实体墙：+x, -x, +z, -z */
  walls: { px: boolean; nx: boolean; pz: boolean; nz: boolean };
  hasCeiling: boolean;
}

export interface BoxInstance {
  id: string;
  /** 底面中心（米） */
  pos: [number, number, number];
  /** 尺寸 w(x), h(y), d(z)（米） */
  dims: [number, number, number];
  /** 底面离地高度；>0 表示悬浮件（层架/吊灯） */
  baseY: number;
  points: number;
}

export interface WhiteboxSpec {
  meta: {
    generator: 'whitebox-web';
    version: string;
    createdWith: { vfovDeg: number; camHeightM: number };
  };
  camera: CameraSpec;
  room: RoomSpec;
  instances: BoxInstance[];
}

export interface GeoDebug {
  /** 采样点云（房间坐标系），xyz 连续存放 */
  points: Float32Array;
  floorInlierRatio: number;
  affine: { a: number; b: number; zNear: number; zFar: number; score: number };
  yawDeg: number;
  ms: number;
}

export interface GeoParams {
  vfovDeg: number;
  pitchDeg: number;
  camHeightM: number;
  minObjSizeM: number;
  maxBoxes: number;
}

export interface GeoResult {
  spec: WhiteboxSpec;
  debug: GeoDebug;
}
