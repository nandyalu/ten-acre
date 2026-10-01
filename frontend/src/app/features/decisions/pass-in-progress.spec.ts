import { TestBed } from '@angular/core/testing';

import { PassInProgress as Pass } from '../../core/models/api.models';
import { AgentService } from '../../core/services/agent.service';
import { PassInProgress } from './pass-in-progress';

const IDLE: Pass = {
  running: false,
  started_at: null,
  woke_because: null,
  turn: null,
  doing: null,
  research: {},
};

class AgentServiceStub {
  reply: Pass = IDLE;
  fail = false;
  calls = 0;

  async getPassInProgress(): Promise<Pass> {
    this.calls++;
    if (this.fail) throw new Error('network error');
    return this.reply;
  }
}

describe('PassInProgress', () => {
  let service: AgentServiceStub;

  beforeEach(async () => {
    service = new AgentServiceStub();
    await TestBed.configureTestingModule({
      imports: [PassInProgress],
      providers: [{ provide: AgentService, useValue: service }],
    }).compileComponents();
  });

  async function render() {
    const fixture = TestBed.createComponent(PassInProgress);
    await fixture.whenStable();
    return fixture;
  }

  it('shows nothing between passes', async () => {
    const el = (await render()).nativeElement as HTMLElement;
    expect(el.textContent?.trim()).toBe('');
    expect(service.calls).toBe(1);
  });

  it('shows why the pass woke, its turn, and the research it waits on', async () => {
    service.reply = {
      running: true,
      started_at: new Date(Date.now() - 4 * 60_000).toISOString(),
      woke_because: 'A resting stop or target closed one of your positions on its own.',
      turn: 2,
      doing: 'Waiting for research on AMZN',
      research: { AMZN: new Date(Date.now() - 3 * 60_000).toISOString() },
    };
    const el = (await render()).nativeElement as HTMLElement;
    const text = el.textContent ?? '';
    expect(text).toContain('A pass is running now');
    expect(text).toContain('A resting stop or target closed');
    expect(text).toContain('Turn 2: Waiting for research on AMZN');
    expect(text).toContain('Research on AMZN');
    expect(text).toContain('3 minutes ago');
  });

  it('hides the card when the API does not answer, rather than claiming a pass', async () => {
    service.fail = true;
    const el = (await render()).nativeElement as HTMLElement;
    expect(el.textContent?.trim()).toBe('');
  });
});
