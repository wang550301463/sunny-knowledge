"""Fixed-label business telemetry; collectors never perform I/O or inspect payloads."""
from __future__ import annotations

import asyncio
import math
import threading
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime

from opentelemetry.trace import Status, StatusCode
from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.core import GaugeMetricFamily

SERVICES = frozenset({'gateway','iam','auth','channel','knowledge','ingest','llm','retrieval','graphiti','agent','mcp'})
OPERATIONS = {
    'model': {'chat','chat_stream','embedding','rerank','usage_commit'},
    'ingest': {'advance','queued','reconcile','register','plan','publish','retry_exhausted','dispatch'},
    'projection': {'lease','project','ack','reconcile','rebuild','delivery'},
    'retrieval': {'search','search_local','scope','authorization','es_hybrid','es_ids','fusion','embedding','rerank','graph','assemble'},
    'graph': {'traverse','walk'},
}
OUTCOMES = frozenset({'success','error','timeout','cancelled','rejected','idle','skipped','advanced','succeeded','failed','review_needed','superseded','projected','already_projected','older_policy_skipped','unchanged','deferred','degraded'})
QUEUES = frozenset({'tasks_queued','tasks_running','tasks_failed','tasks_review_needed','reconcile_due','failed_revisions','outbox_total','outbox_retrieval','outbox_graphiti'})
BUCKETS = (.005,.01,.025,.05,.1,.25,.5,1,2,5,10,30,60,180,300)


def outcome(error):
    if isinstance(error,(asyncio.CancelledError,GeneratorExit)):
        return 'cancelled'
    if isinstance(error,TimeoutError):
        return 'timeout'
    status = getattr(error,'status',getattr(error,'status_code',None))
    if status in (408,504):
        return 'timeout'
    if isinstance(status,int) and 400 <= status < 500:
        return 'rejected'
    return 'error'


@dataclass(frozen=True)
class QueueSnapshot:
    count: int
    oldest: datetime | None


class Operation:
    def __init__(self, metrics, component, operation):
        self.metrics,self.component,self.name = metrics,component,operation
        self.outcome = 'success'

    def __enter__(self):
        self.started = self.metrics.clock()
        component, name = self.metrics.labels(self.component,self.name)
        self.span = self.metrics.tracer.start_as_current_span(
            component+'.'+name,record_exception=False,set_status_on_exception=False
        ) if self.metrics.tracer else nullcontext()
        self.active_span = self.span.__enter__()
        return self

    def __exit__(self, error_type, error, tb):
        if error is not None:
            self.outcome = outcome(error)
        if self.active_span and self.outcome not in {'success','advanced','succeeded','projected','already_projected','older_policy_skipped','unchanged','skipped','idle'}:
            self.active_span.set_status(Status(StatusCode.ERROR))
        self.metrics.record(self.component,self.name,self.outcome,self.metrics.clock()-self.started)
        self.span.__exit__(error_type,error,tb)
        return False


class LocalBudget:
    """Per-request elapsed time minus sequential external-model waits, never a global timer."""
    def __init__(self,metrics):
        self.metrics,self.started,self.excluded = metrics,metrics.clock(),0.0

    @contextmanager
    def external(self, operation):
        started=self.metrics.clock()
        try:
            with self.metrics.operation('retrieval',operation):
                yield
        finally:
            self.excluded += self.metrics.clock()-started

    def elapsed(self):
        return max(0.0,self.metrics.clock()-self.started-self.excluded)


class DomainMetrics:
    def __init__(self,service,registry=None,*,tracer=None,clock=time.monotonic,wall=time.time,stale_after=45):
        self.service=service if service in SERVICES else 'other'
        self.registry=registry if registry is not None else CollectorRegistry()
        self.tracer,self.clock,self.wall,self.stale_after=tracer,clock,wall,stale_after
        labels=['service','component','operation','outcome']
        self.completed=Counter('knowledge_domain_operations_total','Observed operation outcomes; success follows the operation commit/return boundary',labels,registry=self.registry)
        self.duration=Histogram('knowledge_domain_operation_duration_seconds','Operation duration; explicit search_local excludes external model waits',labels,buckets=BUCKETS,registry=self.registry)
        self.delivery_age=Histogram('knowledge_projection_delivery_age_seconds','Observed event creation to acknowledged delivery; not total outbox backlog',['service'],buckets=(1,5,10,30,60,180,300,600,1800,3600,21600,86400),registry=self.registry)
        self.delivery_age_known=Counter('knowledge_projection_delivery_age_observations_total','Whether an acknowledged event supplied a valid creation timestamp',['service','state'],registry=self.registry)
        self._queues={}
        self._lock=threading.Lock()
        self.registry.register(self)

    @staticmethod
    def labels(component,operation):
        if component not in OPERATIONS:
            return 'other','other'
        return component, operation if operation in OPERATIONS[component] else 'other'

    def operation(self,component,operation):
        return Operation(self,component,operation)

    def record(self,component,operation,result,seconds):
        component,operation=self.labels(component,operation)
        result=result if result in OUTCOMES else 'other'
        labels=(self.service,component,operation,result)
        self.completed.labels(*labels).inc()
        self.duration.labels(*labels).observe(max(0.0,seconds))

    async def call(self,component,operation,awaitable):
        with self.operation(component,operation):
            return await awaitable

    def observed_delivery(self,created_at):
        try:
            instant=datetime.fromisoformat(created_at) if isinstance(created_at,str) else created_at
            if not isinstance(instant,datetime) or instant.utcoffset() is None:
                raise ValueError
            age=self.wall()-instant.timestamp()
            if not math.isfinite(age) or age < 0:
                raise ValueError
        except (ValueError,TypeError,OverflowError):
            self.delivery_age_known.labels(self.service,'unknown').inc()
            return
        self.delivery_age_known.labels(self.service,'known').inc()
        self.delivery_age.labels(self.service).observe(age)

    def define_queue(self,queue):
        if queue in QUEUES:
            with self._lock:
                self._queues.setdefault(queue,(None,None))

    def queue_unknown(self,queue):
        if queue in QUEUES:
            with self._lock:
                _,observed=self._queues.get(queue,(None,None))
                self._queues[queue]=(None,observed)

    def queue_snapshot(self,queue,value):
        if queue not in QUEUES:
            return
        if not isinstance(value,QueueSnapshot) or type(value.count) is not int or value.count < 0 or (value.oldest is not None and (not isinstance(value.oldest,datetime) or value.oldest.utcoffset() is None)):
            self.queue_unknown(queue)
            return
        with self._lock:
            self._queues[queue]=(value,self.clock())

    def collect(self):
        description={
            'known':'1 only when the latest authoritative queue snapshot succeeded and is fresh',
            'size':'Queue/state count; NaN when unavailable or stale',
            'oldest_age_seconds':'Age of oldest recorded item; NaN when age is unknown or stale',
            'observed_age_seconds':'Monotonic time since last successful snapshot, including stale data; NaN before first success',
        }
        families={name:GaugeMetricFamily('knowledge_queue_'+name,help,labels=['service','queue']) for name,help in description.items()}
        with self._lock:
            values=dict(self._queues)
        now,wall=self.clock(),self.wall()
        for queue,(value,observed) in values.items():
            age=now-observed if observed is not None else math.nan
            known=value is not None and math.isfinite(age) and age <= self.stale_after
            oldest=math.nan
            if known:
                if value.count == 0:
                    oldest=0
                elif value.oldest is not None and value.oldest.timestamp() <= wall:
                    oldest=wall-value.oldest.timestamp()
            labels=[self.service,queue]
            for name,result in {'known':int(known),'size':value.count if known else math.nan,'oldest_age_seconds':oldest,'observed_age_seconds':age}.items():
                families[name].add_metric(labels,result)
        yield from families.values()


class SnapshotSampler:
    """I/O only on the service event loop. A stopped/hung sampler ages out at scrape time."""
    def __init__(self,metrics,load,queues,*,interval=15,timeout=5):
        self.metrics,self.load,self.queues=metrics,load,tuple(queues)
        self.interval,self.timeout=interval,timeout
        for queue in self.queues:
            metrics.define_queue(queue)

    async def sample(self):
        try:
            async with asyncio.timeout(self.timeout):
                result=await self.load()
            if not isinstance(result,dict) or set(result)!=set(self.queues):
                raise ValueError
            for queue,value in result.items():
                self.metrics.queue_snapshot(queue,value)
            return True
        except asyncio.CancelledError:
            for queue in self.queues:
                self.metrics.queue_unknown(queue)
            raise
        except Exception:  # noqa: BLE001 -- never emit database/provider exception payloads
            for queue in self.queues:
                self.metrics.queue_unknown(queue)
            return False

    async def run(self):
        while True:
            await self.sample()
            await asyncio.sleep(self.interval)