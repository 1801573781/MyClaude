/**
 * 精简 QR 码生成器（零第三方依赖，渲染进程内使用）
 * - 能力：Byte 模式 + 纠错级别 M + 版本 1~10（UTF-8 内容 ≤ 233 字节）
 * - 输出：模块布尔矩阵（true = 深色模块），四周 4 模块静区由调用方渲染
 * - 算法参考 Project Nayuki「QR Code generator」公开实现（MIT License）
 */

export interface QrMatrix {
  /** 每边模块数（版本 v → 4v + 17） */
  size: number;
  /** true = 深色模块；modules[y][x] */
  modules: boolean[][];
}

/** 纠错级别 M 的 format bits（L=01, M=00, Q=11, H=10） */
const ECC_M_FORMAT_BITS = 0b00;

/** 版本 1~10（ECC M）每块纠错码字数（index 0 占位） */
const ECC_CODEWORDS_PER_BLOCK = [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26];
/** 版本 1~10（ECC M）纠错块数（index 0 占位） */
const NUM_ERROR_CORRECTION_BLOCKS = [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5];

/** 惩罚评分常量（ISO/IEC 18004 掩码择优规则） */
const PENALTY_N1 = 3;
const PENALTY_N2 = 3;
const PENALTY_N3 = 40;
const PENALTY_N4 = 10;

/** 版本 v 的原始模块数（含功能图形占位，不含静区） */
function getNumRawDataModules(ver: number): number {
  let result = (16 * ver + 128) * ver + 64;
  if (ver >= 2) {
    const numAlign = Math.floor(ver / 7) + 2;
    result -= (25 * numAlign - 10) * numAlign - 55;
    if (ver >= 7) result -= 36;
  }
  return result;
}

/** 版本 v（ECC M）每块纠错码字数（索引断言，规避 noUncheckedIndexedAccess 的 undefined） */
function eccCodewordsPerBlock(ver: number): number {
  return ECC_CODEWORDS_PER_BLOCK[ver] ?? -1;
}

/** 版本 v（ECC M）纠错块数 */
function numEccBlocks(ver: number): number {
  return NUM_ERROR_CORRECTION_BLOCKS[ver] ?? -1;
}

/** 版本 v（ECC M）的数据码字数 */
function getNumDataCodewords(ver: number): number {
  return (
    Math.floor(getNumRawDataModules(ver) / 8) -
    eccCodewordsPerBlock(ver) * numEccBlocks(ver)
  );
}

/** GF(256) 乘法（本原多项式 0x11D） */
function gfMul(x: number, y: number): number {
  let z = 0;
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d);
    z ^= ((y >>> i) & 1) * x;
  }
  return z & 0xff;
}

/** RS 生成多项式（除数）：(x-α^0)(x-α^1)…(x-α^(degree-1)) */
function reedSolomonComputeDivisor(degree: number): number[] {
  const result: number[] = [];
  for (let i = 0; i < degree - 1; i++) result.push(0);
  result.push(1); // x^0 的系数
  let root = 1;
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < result.length; j++) {
      const cur = result[j] ?? 0;
      const mul = gfMul(cur, root);
      result[j] = j + 1 < result.length ? mul ^ (result[j + 1] ?? 0) : mul;
    }
    root = gfMul(root, 0x02);
  }
  return result;
}

/** RS 余数（纠错码字） */
function reedSolomonComputeRemainder(data: readonly number[], divisor: readonly number[]): number[] {
  const result = divisor.map(() => 0);
  for (const b of data) {
    const factor = b ^ (result.shift() ?? 0);
    result.push(0);
    for (let i = 0; i < divisor.length; i++) {
      const coef = divisor[i] ?? 0;
      result[i] = (result[i] ?? 0) ^ gfMul(coef, factor);
    }
  }
  return result;
}

/** 纠错交织：数据码字 → 分块加 ECC → 按列交织（短块填充位跳过） */
function addEccAndInterleave(ver: number, data: readonly number[]): number[] {
  const numBlocks = numEccBlocks(ver);
  const blockEccLen = eccCodewordsPerBlock(ver);
  const rawCodewords = Math.floor(getNumRawDataModules(ver) / 8);
  const numShortBlocks = numBlocks - (rawCodewords % numBlocks);
  const shortBlockLen = Math.floor(rawCodewords / numBlocks);

  const rsDiv = reedSolomonComputeDivisor(blockEccLen);
  const blocks: number[][] = [];
  for (let i = 0, k = 0; i < numBlocks; i++) {
    const dat = data.slice(k, k + shortBlockLen - blockEccLen + (i < numShortBlocks ? 0 : 1));
    k += dat.length;
    const ecc = reedSolomonComputeRemainder(dat, rsDiv);
    if (i < numShortBlocks) dat.push(0); // 填充位在交织时跳过
    blocks.push(dat.concat(ecc));
  }

  const result: number[] = [];
  const blockLen = shortBlockLen + 1; // 各块等长（短块含 1 个填充位）
  for (let i = 0; i < blockLen; i++) {
    for (let j = 0; j < blocks.length; j++) {
      if (i !== shortBlockLen - blockEccLen || j >= numShortBlocks) result.push(blocks[j]![i]!);
    }
  }
  return result;
}

/** 对齐模式中心坐标（版本 1 无） */
function getAlignmentPositions(version: number, size: number): number[] {
  if (version === 1) return [];
  const numAlign = Math.floor(version / 7) + 2;
  const step = version === 32 ? 26 : Math.ceil((version * 4 + 4) / (numAlign * 2 - 2)) * 2;
  const result = [6];
  for (let pos = size - 7; result.length < numAlign; pos -= step) result.splice(1, 0, pos);
  return result;
}

/** 掩码惩罚评分（行/列 run、finder 样式、2×2 块、暗模块比例） */
function getPenaltyScore(modules: boolean[][], size: number): number {
  let result = 0;

  const addHistory = (runLength: number, hist: number[]): void => {
    hist.pop();
    hist.unshift(runLength);
  };
  const countPatterns = (hist: number[]): number => {
    const n = hist[1] ?? 0;
    if (!(n > 0 && hist[2] === n && hist[3] === n * 3 && hist[4] === n && hist[5] === n)) return 0;
    return ((hist[0] ?? 0) >= n * 4 && (hist[6] ?? 0) >= n ? 1 : 0) + ((hist[6] ?? 0) >= n * 4 && (hist[0] ?? 0) >= n ? 1 : 0);
  };
  const terminateAndCount = (runColor: boolean, runLength: number, hist: number[]): number => {
    if (runColor) {
      addHistory(runLength, hist);
      runLength = 0;
    }
    runLength += size; // 尾部 light 边界
    addHistory(runLength, hist);
    return countPatterns(hist);
  };

  // 行：连续同色 run + finder 样式
  for (let y = 0; y < size; y++) {
    let runColor = false;
    let runLen = 0;
    const hist = [0, 0, 0, 0, 0, 0, 0];
    for (let x = 0; x < size; x++) {
      if (modules[y]![x]! === runColor) {
        runLen++;
        if (runLen === 5) result += PENALTY_N1;
        else if (runLen > 5) result += 1;
      } else {
        addHistory(runLen, hist);
        if (!runColor) result += countPatterns(hist) * PENALTY_N3;
        runColor = modules[y]![x]!;
        runLen = 1;
      }
    }
    result += terminateAndCount(runColor, runLen, hist) * PENALTY_N3;
  }

  // 列：同上
  for (let x = 0; x < size; x++) {
    let runColor = false;
    let runLen = 0;
    const hist = [0, 0, 0, 0, 0, 0, 0];
    for (let y = 0; y < size; y++) {
      if (modules[y]![x]! === runColor) {
        runLen++;
        if (runLen === 5) result += PENALTY_N1;
        else if (runLen > 5) result += 1;
      } else {
        addHistory(runLen, hist);
        if (!runColor) result += countPatterns(hist) * PENALTY_N3;
        runColor = modules[y]![x]!;
        runLen = 1;
      }
    }
    result += terminateAndCount(runColor, runLen, hist) * PENALTY_N3;
  }

  // 2×2 同色块
  for (let y = 0; y < size - 1; y++) {
    const row0 = modules[y]!;
    const row1 = modules[y + 1]!;
    for (let x = 0; x < size - 1; x++) {
      const c = row0[x]!;
      if (c === row0[x + 1]! && c === row1[x]! && c === row1[x + 1]!) result += PENALTY_N2;
    }
  }

  // 暗模块比例偏离 50%
  let dark = 0;
  for (const row of modules) for (const c of row) if (c) dark++;
  const total = size * size;
  const k = Math.ceil(Math.abs(dark * 20 - total * 10) / total) - 1;
  result += k * PENALTY_N4;

  return result;
}

/** 矩阵绘制：功能图形 + 数据蛇形放置 + 掩码择优 */
function buildMatrix(version: number, dataCodewords: number[]): QrMatrix {
  const size = version * 4 + 17;
  const modules: boolean[][] = [];
  const isFunction: boolean[][] = [];
  for (let i = 0; i < size; i++) {
    modules.push(new Array<boolean>(size).fill(false));
    isFunction.push(new Array<boolean>(size).fill(false));
  }

  const setFn = (x: number, y: number, dark: boolean): void => {
    modules[y]![x] = dark;
    isFunction[y]![x] = true;
  };

  // timing patterns（第 6 行/列）
  for (let i = 0; i < size; i++) {
    setFn(6, i, i % 2 === 0);
    setFn(i, 6, i % 2 === 0);
  }

  // finder patterns（三个角 7×7）
  const drawFinder = (cx: number, cy: number): void => {
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        const dist = Math.max(Math.abs(dx), Math.abs(dy));
        const x = cx + dx;
        const y = cy + dy;
        if (x >= 0 && x < size && y >= 0 && y < size) setFn(x, y, dist !== 2 && dist !== 4);
      }
    }
  };
  drawFinder(3, 3);
  drawFinder(size - 4, 3);
  drawFinder(3, size - 4);

  // alignment patterns（5×5，避开 finder）
  const alignPos = getAlignmentPositions(version, size);
  const numAlign = alignPos.length;
  for (let i = 0; i < numAlign; i++) {
    for (let j = 0; j < numAlign; j++) {
      if ((i === 0 && j === 0) || (i === 0 && j === numAlign - 1) || (i === numAlign - 1 && j === 0)) continue;
      const cx = alignPos[i] ?? 0;
      const cy = alignPos[j] ?? 0;
      for (let dy = -2; dy <= 2; dy++) {
        for (let dx = -2; dx <= 2; dx++) {
          setFn(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
        }
      }
    }
  }

  // format bits（ECC 级别 + 掩码，BCH(15,5) 编码后 XOR 0x5412；两份冗余）
  const drawFormatBits = (mask: number): void => {
    const data = (ECC_M_FORMAT_BITS << 3) | mask;
    let rem = data;
    for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
    const bits = ((data << 10) | rem) ^ 0x5412;
    const bit = (i: number): boolean => ((bits >>> i) & 1) !== 0;
    for (let i = 0; i <= 5; i++) setFn(8, i, bit(i));
    setFn(8, 7, bit(6));
    setFn(8, 8, bit(7));
    setFn(7, 8, bit(8));
    for (let i = 9; i < 15; i++) setFn(14 - i, 8, bit(i));
    for (let i = 0; i < 8; i++) setFn(size - 1 - i, 8, bit(i));
    for (let i = 8; i < 15; i++) setFn(8, size - 15 + i, bit(i));
    setFn(size - 8, 8, true); // dark module（恒黑）
  };

  // version info（版本 ≥ 7 才有，BCH(18,6)，两份冗余）
  const drawVersion = (): void => {
    if (version < 7) return;
    let rem = version;
    for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
    const bits = (version << 12) | rem;
    for (let i = 0; i < 18; i++) {
      const color = ((bits >>> i) & 1) !== 0;
      const a = size - 11 + (i % 3);
      const b = Math.floor(i / 3);
      setFn(a, b, color);
      setFn(b, a, color);
    }
  };

  drawFormatBits(0); // 占位标记 format 区为功能模块
  drawVersion();

  // 数据蛇形放置（右下起，两列一组向上/向下交替）
  const allCodewords = addEccAndInterleave(version, dataCodewords);
  {
    let i = 0;
    for (let right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5; // 跳过 timing 列
      for (let vert = 0; vert < size; vert++) {
        for (let j = 0; j < 2; j++) {
          const x = right - j;
          const upward = ((right + 1) & 2) === 0;
          const y = upward ? size - 1 - vert : vert;
          if (!isFunction[y]![x]! && i < allCodewords.length * 8) {
            modules[y]![x] = ((allCodewords[i >>> 3]! >>> (7 - (i & 7))) & 1) !== 0;
            i++;
          }
        }
      }
    }
  }

  // 掩码（仅 XOR 数据模块；8 种掩码中取惩罚最小者）
  const applyMask = (mask: number): void => {
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        if (isFunction[y]![x]!) continue;
        let invert: boolean;
        switch (mask) {
          case 0:
            invert = (x + y) % 2 === 0;
            break;
          case 1:
            invert = y % 2 === 0;
            break;
          case 2:
            invert = x % 3 === 0;
            break;
          case 3:
            invert = (x + y) % 3 === 0;
            break;
          case 4:
            invert = (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0;
            break;
          case 5:
            invert = ((x * y) % 2) + ((x * y) % 3) === 0;
            break;
          case 6:
            invert = (((x * y) % 2) + ((x * y) % 3)) % 2 === 0;
            break;
          default:
            invert = (((x + y) % 2) + ((x * y) % 3)) % 2 === 0;
            break;
        }
        modules[y]![x] = modules[y]![x]! !== invert;
      }
    }
  };

  let bestMask = 0;
  let minScore = Infinity;
  for (let mask = 0; mask < 8; mask++) {
    drawFormatBits(mask);
    applyMask(mask);
    const score = getPenaltyScore(modules, size);
    if (score < minScore) {
      minScore = score;
      bestMask = mask;
    }
    applyMask(mask); // XOR 自身撤销，恢复无掩码状态
  }
  drawFormatBits(bestMask);
  applyMask(bestMask);

  return { size, modules };
}

/** 编码文本为 QR 矩阵（UTF-8，自动选版本 1~10） */
export function encodeText(text: string): QrMatrix {
  const data = Array.from(new TextEncoder().encode(text));

  // 版本选择：Byte 模式头（4 bit）+ 字符计数（ver1-9: 8 bit / ver10+: 16 bit）+ 数据
  let version = -1;
  for (let v = 1; v <= 10; v++) {
    const capBits = getNumDataCodewords(v) * 8;
    if (4 + (v <= 9 ? 8 : 16) + data.length * 8 <= capBits) {
      version = v;
      break;
    }
  }
  if (version < 0) {
    throw new Error(`二维码内容过长（${data.length} 字节，超出版本 10 / ECC M 容量 213 字节）`);
  }

  // 位流：模式 + 计数 + 数据 + 终止符 + 填充（0xEC/0x11 交替）
  const bb: number[] = [];
  let bitBuf = 0;
  let bitCount = 0;
  const appendBits = (val: number, len: number): void => {
    for (let i = len - 1; i >= 0; i--) {
      bitBuf = (bitBuf << 1) | ((val >>> i) & 1);
      bitCount++;
      if (bitCount === 8) {
        bb.push(bitBuf);
        bitBuf = 0;
        bitCount = 0;
      }
    }
  };
  const totalBits = (): number => bb.length * 8 + bitCount;

  const capBits = getNumDataCodewords(version) * 8;
  appendBits(0b0100, 4); // Byte 模式
  appendBits(data.length, version <= 9 ? 8 : 16);
  for (const b of data) appendBits(b, 8);
  appendBits(0, Math.min(4, capBits - totalBits())); // 终止符
  appendBits(0, (8 - (totalBits() % 8)) % 8); // 补齐字节边界
  for (let pad = 0xec; totalBits() < capBits; pad ^= 0xec ^ 0x11) appendBits(pad, 8);

  return buildMatrix(version, bb);
}
