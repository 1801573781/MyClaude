/**
 * auth 服务（FR-21：微信扫码登录、积分余额与消耗明细）
 * - 调云端 REST：POST /v1/auth/wechat-qr、GET /v1/account/credits
 * - token 经 safeStorage 加密后落盘（密钥不落明文，NFR-03）
 */
import type { WeChatQrResponse, CreditsInfo } from '@wdbuddy/shared-types';

export interface AuthTokens {
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
}

export class AuthService {
  private tokens: AuthTokens | null = null;

  constructor(
    private readonly apiBase: string,
    /** token 加密存储回调（主进程注入 safeStorage 实现） */
    private readonly secureStorage: {
      set(key: string, value: string): void;
      get(key: string): string | null;
      delete(key: string): void;
    }
  ) {}

  get isLoggedIn(): boolean {
    return this.tokens !== null && this.tokens.expiresAt > Date.now();
  }

  /** 请求微信扫码二维码 */
  async requestWeChatQr(): Promise<WeChatQrResponse> {
    const res = await fetch(`${this.apiBase}/v1/auth/wechat-qr`, { method: 'POST' });
    if (!res.ok) throw new Error(`获取二维码失败：HTTP ${res.status}`);
    return (await res.json()) as WeChatQrResponse;
  }

  /** 扫码结果轮询 → 换取 token（安全存储） */
  async pollLogin(sceneId: string): Promise<boolean> {
    const res = await fetch(`${this.apiBase}/v1/auth/wechat-qr/${sceneId}`, { method: 'GET' });
    if (!res.ok) return false;
    const data = (await res.json()) as { status: string; tokens?: AuthTokens };
    if (data.status === 'confirmed' && data.tokens) {
      this.tokens = data.tokens;
      this.secureStorage.set('auth-tokens', JSON.stringify(data.tokens));
      return true;
    }
    return false;
  }

  /** 启动时恢复会话 */
  restore(): boolean {
    const raw = this.secureStorage.get('auth-tokens');
    if (!raw) return false;
    try {
      this.tokens = JSON.parse(raw) as AuthTokens;
      return this.isLoggedIn;
    } catch {
      this.secureStorage.delete('auth-tokens');
      return false;
    }
  }

  logout(): void {
    this.tokens = null;
    this.secureStorage.delete('auth-tokens');
  }

  /** 积分余额与消耗明细 */
  async getCredits(): Promise<CreditsInfo> {
    if (!this.tokens) throw new Error('未登录');
    const res = await fetch(`${this.apiBase}/v1/account/credits`, {
      headers: { Authorization: `Bearer ${this.tokens.accessToken}` },
    });
    if (!res.ok) throw new Error(`查询积分失败：HTTP ${res.status}`);
    return (await res.json()) as CreditsInfo;
  }
}
