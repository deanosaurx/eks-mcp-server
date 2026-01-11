#!/usr/bin/env python3
# /// script
# dependencies = [
#   "mcp>=1.0.0",
#   "boto3>=1.34.0",
#   "kubernetes>=28.1.0",
# ]
# ///
"""
Simplified EKS MCP Server for Learning
This demonstrates how to build an MCP server that provides tools for querying EKS clusters.
"""

import asyncio
import json
import sys
from typing import Any, Optional
from datetime import datetime

# MCP SDK - you'll install this with: pip install mcp
from mcp.server import Server
from mcp.types import (
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
)
import mcp.server.stdio

import boto3
from botocore.exceptions import ClientError

from kubernetes import client, config
from kubernetes.client.rest import ApiException


class EKSMCPServer:
    """MCP Server for Amazon EKS operations"""
    
    def __init__(self, allow_write: bool = False, allow_sensitive: bool = False):
        self.allow_write = allow_write
        self.allow_sensitive = allow_sensitive
        self.server = Server("eks-mcp-server")
        self.eks_client = boto3.client('eks', region_name='eu-central-1')
        self.k8s_clients = {}
        
        self._register_handlers()
    
    def _register_handlers(self):
        """Register all MCP protocol handlers"""
        
        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            """Return list of available tools"""
            tools = [
                Tool(
                    name="list_clusters",
                    description="List all EKS clusters in the current region",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    }
                ),
                Tool(
                    name="describe_cluster",
                    description="Get detailed information about a specific EKS cluster",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "cluster_name": {
                                "type": "string",
                                "description": "Name of the EKS cluster"
                            }
                        },
                        "required": ["cluster_name"]
                    }
                ),
                Tool(
                    name="list_k8s_resources",
                    description="List Kubernetes resources (pods, services, deployments, etc.) in a cluster",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "cluster_name": {
                                "type": "string",
                                "description": "Name of the EKS cluster"
                            },
                            "resource_type": {
                                "type": "string",
                                "description": "Type of resource (pod, service, deployment, etc.)",
                                "enum": ["pod", "service", "deployment", "configmap", "secret", "ingress", "node"]
                            },
                            "namespace": {
                                "type": "string",
                                "description": "Kubernetes namespace (default: all namespaces)",
                                "default": "all"
                            }
                        },
                        "required": ["cluster_name", "resource_type"]
                    }
                ),
                Tool(
                    name="get_resource_details",
                    description="Get detailed information about a specific Kubernetes resource",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "cluster_name": {"type": "string"},
                            "resource_type": {"type": "string"},
                            "resource_name": {"type": "string"},
                            "namespace": {
                                "type": "string",
                                "description": "Namespace (not needed for cluster-scoped resources like nodes)"
                            }
                        },
                        "required": ["cluster_name", "resource_type", "resource_name"]
                    }
                ),
                Tool(
                    name="get_pod_metrics",
                    description="Get CPU and memory metrics for pods from the metrics-server",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "cluster_name": {
                                "type": "string",
                                "description": "Name of the EKS cluster"
                            },
                            "namespace": {
                                "type": "string",
                                "description": "Kubernetes namespace (default: all namespaces)",
                                "default": "all"
                            },
                            "pod_name": {
                                "type": "string",
                                "description": "Specific pod name (optional, returns all pods if not specified)"
                            }
                        },
                        "required": ["cluster_name"]
                    }
                )
            ]
            
            if self.allow_sensitive:
                tools.extend([
                    Tool(
                        name="get_pod_logs",
                        description="Retrieve logs from a specific pod",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "cluster_name": {"type": "string"},
                                "pod_name": {"type": "string"},
                                "namespace": {"type": "string"},
                                "container_name": {
                                    "type": "string",
                                    "description": "Specific container name (optional for single-container pods)"
                                },
                                "tail_lines": {
                                    "type": "integer",
                                    "description": "Number of lines to retrieve from the end",
                                    "default": 100
                                }
                            },
                            "required": ["cluster_name", "pod_name", "namespace"]
                        }
                    ),
                    Tool(
                        name="get_events",
                        description="Get Kubernetes events for troubleshooting",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "cluster_name": {"type": "string"},
                                "namespace": {
                                    "type": "string",
                                    "description": "Namespace to get events from (default: all)"
                                },
                                "resource_name": {
                                    "type": "string",
                                    "description": "Filter events for a specific resource"
                                }
                            },
                            "required": ["cluster_name"]
                        }
                    )
                ])
            
            if self.allow_write:
                tools.extend([
                    Tool(
                        name="delete_pod",
                        description="Delete a specific pod (requires --allow-write flag)",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "cluster_name": {"type": "string"},
                                "pod_name": {"type": "string"},
                                "namespace": {"type": "string"}
                            },
                            "required": ["cluster_name", "pod_name", "namespace"]
                        }
                    ),
                    Tool(
                        name="scale_deployment",
                        description="Scale a deployment to a specific number of replicas (requires --allow-write flag)",
                        inputSchema={
                            "type": "object",
                            "properties": {
                                "cluster_name": {"type": "string"},
                                "deployment_name": {"type": "string"},
                                "namespace": {"type": "string"},
                                "replicas": {"type": "integer"}
                            },
                            "required": ["cluster_name", "deployment_name", "namespace", "replicas"]
                        }
                    )
                ])
            
            return tools
        
        @self.server.call_tool()
        async def call_tool(name: str, arguments: Any) -> list[TextContent]:
            """Handle tool execution"""
            print(f"!!! CALL_TOOL INVOKED: {name} with args {arguments}", file=sys.stderr, flush=True)
            
            try:
                if name == "list_clusters":
                    result = await self._list_clusters()
                elif name == "describe_cluster":
                    result = await self._describe_cluster(arguments["cluster_name"])
                elif name == "list_k8s_resources":
                    result = await self._list_k8s_resources(
                        arguments["cluster_name"],
                        arguments["resource_type"],
                        arguments.get("namespace", "all")
                    )
                elif name == "get_resource_details":
                    result = await self._get_resource_details(
                        arguments["cluster_name"],
                        arguments["resource_type"],
                        arguments["resource_name"],
                        arguments.get("namespace")
                    )
                elif name == "get_pod_logs" and self.allow_sensitive:
                    result = await self._get_pod_logs(
                        arguments["cluster_name"],
                        arguments["pod_name"],
                        arguments["namespace"],
                        arguments.get("container_name"),
                        arguments.get("tail_lines", 100)
                    )
                elif name == "get_events" and self.allow_sensitive:
                    result = await self._get_events(
                        arguments["cluster_name"],
                        arguments.get("namespace"),
                        arguments.get("resource_name")
                    )
                elif name == "delete_pod" and self.allow_write:
                    result = await self._delete_pod(
                        arguments["cluster_name"],
                        arguments["pod_name"],
                        arguments["namespace"]
                    )
                elif name == "scale_deployment" and self.allow_write:
                    result = await self._scale_deployment(
                        arguments["cluster_name"],
                        arguments["deployment_name"],
                        arguments["namespace"],
                        arguments["replicas"]
                    )
                elif name == "get_pod_metrics":
                    result = await self._get_pod_metrics(
                        arguments["cluster_name"],
                        arguments.get("namespace", "all"),
                        arguments.get("pod_name")
                    )
                else:
                    result = {
                        "error": f"Tool '{name}' not found or not allowed with current permissions"
                    }
                
                return [TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, default=str)
                )]
                
            except Exception as e:
                return [TextContent(
                    type="text",
                    text=json.dumps({
                        "error": str(e),
                        "type": type(e).__name__
                    }, indent=2)
                )]
    
    async def _list_clusters(self) -> dict:
        """List all EKS clusters"""
        try:
            response = self.eks_client.list_clusters()
            return {
                "clusters": response.get('clusters', []),
                "count": len(response.get('clusters', []))
            }
        except ClientError as e:
            return {"error": str(e)}
    
    async def _describe_cluster(self, cluster_name: str) -> dict:
        """Get detailed cluster information"""
        try:
            response = self.eks_client.describe_cluster(name=cluster_name)
            cluster = response['cluster']
            return {
                "name": cluster['name'],
                "status": cluster['status'],
                "version": cluster['version'],
                "endpoint": cluster['endpoint'],
                "created_at": cluster['createdAt'],
                "platform_version": cluster.get('platformVersion'),
                "vpc_config": {
                    "vpc_id": cluster['resourcesVpcConfig'].get('vpcId'),
                    "subnet_ids": cluster['resourcesVpcConfig'].get('subnetIds', []),
                    "security_group_ids": cluster['resourcesVpcConfig'].get('securityGroupIds', []),
                    "endpoint_public_access": cluster['resourcesVpcConfig'].get('endpointPublicAccess'),
                    "endpoint_private_access": cluster['resourcesVpcConfig'].get('endpointPrivateAccess'),
                }
            }
        except ClientError as e:
            return {"error": str(e)}
    

    def _get_k8s_client(self, cluster_name: str):
        """Get or create a Kubernetes client for the cluster"""
        if cluster_name not in self.k8s_clients:
            try:
                print(f"Loading kubeconfig for cluster: {cluster_name}", file=sys.stderr, flush=True)
                config.load_kube_config()
                print(f"Kubeconfig loaded successfully", file=sys.stderr, flush=True)
                
                self.k8s_clients[cluster_name] = {
                    'core_v1': client.CoreV1Api(),
                    'apps_v1': client.AppsV1Api(),
                    'networking_v1': client.NetworkingV1Api(),
                    'custom_objects': client.CustomObjectsApi()
                }
                print(f"K8s clients created successfully", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"Failed to load kubeconfig, falling back to token generation: {e}", file=sys.stderr, flush=True)
                cluster_info = self.eks_client.describe_cluster(name=cluster_name)
                cluster = cluster_info['cluster']
                
                configuration = client.Configuration()
                configuration.host = cluster['endpoint']
                configuration.verify_ssl = True
                configuration.ssl_ca_cert = self._get_cluster_ca(cluster)
                
                token = self._get_bearer_token(cluster_name)
                configuration.api_key = {"authorization": f"Bearer {token}"}
                
                api_client = client.ApiClient(configuration)
                self.k8s_clients[cluster_name] = {
                    'core_v1': client.CoreV1Api(api_client),
                    'apps_v1': client.AppsV1Api(api_client),
                    'networking_v1': client.NetworkingV1Api(api_client),
                    'custom_objects': client.CustomObjectsApi()
                }
        
        return self.k8s_clients[cluster_name]

    def _get_cluster_ca(self, cluster: dict) -> str:
        """Extract and save cluster CA certificate"""
        import base64
        import tempfile
        
        ca_cert = base64.b64decode(cluster['certificateAuthority']['data'])
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.crt') as f:
            f.write(ca_cert)
            return f.name
    
    def _get_bearer_token(self, cluster_name: str) -> str:
        """Generate EKS authentication token"""
        import base64
        from botocore.signers import RequestSigner
        
        sts_client = boto3.client('sts')
        service_id = sts_client.meta.service_model.service_id
        
        signer = RequestSigner(
            service_id,
            sts_client.meta.region_name,
            'sts',
            'v4',
            sts_client._request_signer._credentials,
            sts_client.meta.events
        )
        
        params = {
            'method': 'GET',
            'url': f'https://sts.{sts_client.meta.region_name}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15',
            'body': {},
            'headers': {
                'x-k8s-aws-id': cluster_name
            },
            'context': {}
        }
        
        signed_url = signer.generate_presigned_url(
            params,
            region_name=sts_client.meta.region_name,
            expires_in=60,
            operation_name=''
        )
        
        token = f'k8s-aws-v1.{base64.urlsafe_b64encode(signed_url.encode()).decode().rstrip("=")}'
        return token
    
    async def _list_k8s_resources(self, cluster_name: str, resource_type: str, namespace: str) -> dict:
        """List Kubernetes resources"""
        print(f"=== _list_k8s_resources called ===", file=sys.stderr)
        print(f"Cluster: {cluster_name}, Type: {resource_type}, Namespace: {namespace}", file=sys.stderr)
        try:
            print(f"Getting k8s client...", file=sys.stderr)
            k8s = self._get_k8s_client(cluster_name)
            print(f"K8s client obtained: {k8s}", file=sys.stderr)
            
            if resource_type == "pod":
                if namespace == "all":
                    items = k8s['core_v1'].list_pod_for_all_namespaces().items
                else:
                    items = k8s['core_v1'].list_namespaced_pod(namespace).items
                
                return {
                    "resource_type": "pods",
                    "count": len(items),
                    "items": [
                        {
                            "name": pod.metadata.name,
                            "namespace": pod.metadata.namespace,
                            "status": pod.status.phase,
                            "ready": f"{sum(1 for c in pod.status.container_statuses if c.ready)}/{len(pod.status.container_statuses)}" if pod.status.container_statuses else "0/0",
                            "restarts": sum(c.restart_count for c in pod.status.container_statuses) if pod.status.container_statuses else 0,
                            "age": str(datetime.now(pod.metadata.creation_timestamp.tzinfo) - pod.metadata.creation_timestamp)
                        }
                        for pod in items
                    ]
                }
            
            elif resource_type == "service":
                if namespace == "all":
                    items = k8s['core_v1'].list_service_for_all_namespaces().items
                else:
                    items = k8s['core_v1'].list_namespaced_service(namespace).items
                
                return {
                    "resource_type": "services",
                    "count": len(items),
                    "items": [
                        {
                            "name": svc.metadata.name,
                            "namespace": svc.metadata.namespace,
                            "type": svc.spec.type,
                            "cluster_ip": svc.spec.cluster_ip,
                            "external_ip": svc.status.load_balancer.ingress[0].ip if svc.status.load_balancer and svc.status.load_balancer.ingress else None,
                            "ports": [f"{p.port}/{p.protocol}" for p in svc.spec.ports] if svc.spec.ports else []
                        }
                        for svc in items
                    ]
                }
            
            elif resource_type == "deployment":
                if namespace == "all":
                    items = k8s['apps_v1'].list_deployment_for_all_namespaces().items
                else:
                    items = k8s['apps_v1'].list_namespaced_deployment(namespace).items
                
                return {
                    "resource_type": "deployments",
                    "count": len(items),
                    "items": [
                        {
                            "name": dep.metadata.name,
                            "namespace": dep.metadata.namespace,
                            "ready": f"{dep.status.ready_replicas or 0}/{dep.status.replicas or 0}",
                            "up_to_date": dep.status.updated_replicas or 0,
                            "available": dep.status.available_replicas or 0,
                            "age": str(datetime.now(dep.metadata.creation_timestamp.tzinfo) - dep.metadata.creation_timestamp)
                        }
                        for dep in items
                    ]
                }
            
            elif resource_type == "node":
                items = k8s['core_v1'].list_node().items
                
                return {
                    "resource_type": "nodes",
                    "count": len(items),
                    "items": [
                        {
                            "name": node.metadata.name,
                            "status": next((c.type for c in node.status.conditions if c.status == "True"), "Unknown"),
                            "roles": ",".join([k.replace("node-role.kubernetes.io/", "") for k in node.metadata.labels.keys() if "node-role" in k]) or "none",
                            "version": node.status.node_info.kubelet_version,
                            "instance_type": node.metadata.labels.get("node.kubernetes.io/instance-type", "unknown"),
                            "age": str(datetime.now(node.metadata.creation_timestamp.tzinfo) - node.metadata.creation_timestamp)
                        }
                        for node in items
                    ]
                }
            
            else:
                return {"error": f"Resource type '{resource_type}' not implemented"}
                
        except ApiException as e:
            print(f"ApiException caught: {e.status} - {e.reason}", file=sys.stderr)
            return {"error": f"Kubernetes API error: {e.status} - {e.reason}"}
        except Exception as e:
            print(f"Generic Exception caught: {type(e).__name__}: {str(e)}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return {"error": str(e)}
    
    async def _get_resource_details(self, cluster_name: str, resource_type: str, 
                                   resource_name: str, namespace: Optional[str]) -> dict:
        """Get detailed information about a specific resource"""
        try:
            k8s = self._get_k8s_client(cluster_name)
            
            if resource_type == "pod":
                pod = k8s['core_v1'].read_namespaced_pod(resource_name, namespace)
                return {
                    "name": pod.metadata.name,
                    "namespace": pod.metadata.namespace,
                    "status": pod.status.phase,
                    "node": pod.spec.node_name,
                    "ip": pod.status.pod_ip,
                    "labels": pod.metadata.labels,
                    "containers": [
                        {
                            "name": c.name,
                            "image": c.image,
                            "ready": next((cs.ready for cs in pod.status.container_statuses if cs.name == c.name), False),
                            "restart_count": next((cs.restart_count for cs in pod.status.container_statuses if cs.name == c.name), 0)
                        }
                        for c in pod.spec.containers
                    ],
                    "conditions": [
                        {"type": c.type, "status": c.status, "reason": c.reason}
                        for c in (pod.status.conditions or [])
                    ]
                }
            
            elif resource_type == "deployment":
                dep = k8s['apps_v1'].read_namespaced_deployment(resource_name, namespace)
                return {
                    "name": dep.metadata.name,
                    "namespace": dep.metadata.namespace,
                    "replicas": {
                        "desired": dep.spec.replicas,
                        "ready": dep.status.ready_replicas or 0,
                        "available": dep.status.available_replicas or 0,
                        "updated": dep.status.updated_replicas or 0
                    },
                    "selector": dep.spec.selector.match_labels,
                    "strategy": dep.spec.strategy.type,
                    "containers": [
                        {"name": c.name, "image": c.image}
                        for c in dep.spec.template.spec.containers
                    ]
                }
            
            else:
                return {"error": f"Resource type '{resource_type}' not implemented for details"}
                
        except ApiException as e:
            return {"error": f"Kubernetes API error: {e.status} - {e.reason}"}
    
    async def _get_pod_logs(self, cluster_name: str, pod_name: str, namespace: str,
                           container_name: Optional[str], tail_lines: int) -> dict:
        """Get pod logs"""
        if not self.allow_sensitive:
            return {"error": "Sensitive data access not allowed. Use --allow-sensitive-data-access flag"}
        
        try:
            k8s = self._get_k8s_client(cluster_name)
            logs = k8s['core_v1'].read_namespaced_pod_log(
                pod_name,
                namespace,
                container=container_name,
                tail_lines=tail_lines
            )
            
            return {
                "pod": pod_name,
                "namespace": namespace,
                "container": container_name or "default",
                "lines": tail_lines,
                "logs": logs
            }
        except ApiException as e:
            return {"error": f"Kubernetes API error: {e.status} - {e.reason}"}
    
    async def _get_events(self, cluster_name: str, namespace: Optional[str], 
                         resource_name: Optional[str]) -> dict:
        """Get Kubernetes events"""
        if not self.allow_sensitive:
            return {"error": "Sensitive data access not allowed. Use --allow-sensitive-data-access flag"}
        
        try:
            k8s = self._get_k8s_client(cluster_name)
            
            if namespace:
                events = k8s['core_v1'].list_namespaced_event(namespace).items
            else:
                events = k8s['core_v1'].list_event_for_all_namespaces().items
            
            if resource_name:
                events = [e for e in events if e.involved_object.name == resource_name]
            
            events.sort(key=lambda e: e.last_timestamp or e.event_time or datetime.min, reverse=True)
            
            return {
                "count": len(events),
                "events": [
                    {
                        "type": e.type,
                        "reason": e.reason,
                        "message": e.message,
                        "object": f"{e.involved_object.kind}/{e.involved_object.name}",
                        "namespace": e.involved_object.namespace,
                        "count": e.count,
                        "first_seen": str(e.first_timestamp),
                        "last_seen": str(e.last_timestamp or e.event_time)
                    }
                    for e in events[:50]
                ]
            }
        except ApiException as e:
            return {"error": f"Kubernetes API error: {e.status} - {e.reason}"}
    
    async def _delete_pod(self, cluster_name: str, pod_name: str, namespace: str) -> dict:
        """Delete a pod"""
        if not self.allow_write:
            return {"error": "Write operations not allowed. Server is in read-only mode."}
        
        try:
            k8s = self._get_k8s_client(cluster_name)
            k8s['core_v1'].delete_namespaced_pod(pod_name, namespace)
            return {
                "status": "success",
                "message": f"Pod '{pod_name}' in namespace '{namespace}' deleted"
            }
        except ApiException as e:
            return {"error": f"Kubernetes API error: {e.status} - {e.reason}"}
    
    async def _scale_deployment(self, cluster_name: str, deployment_name: str, 
                               namespace: str, replicas: int) -> dict:
        """Scale a deployment"""
        if not self.allow_write:
            return {"error": "Write operations not allowed. Server is in read-only mode."}
        
        try:
            k8s = self._get_k8s_client(cluster_name)
            
            deployment = k8s['apps_v1'].read_namespaced_deployment(deployment_name, namespace)
            
            deployment.spec.replicas = replicas
            
            k8s['apps_v1'].patch_namespaced_deployment(deployment_name, namespace, deployment)
            
            return {
                "status": "success",
                "message": f"Deployment '{deployment_name}' scaled to {replicas} replicas"
            }
        except ApiException as e:
            return {"error": f"Kubernetes API error: {e.status} - {e.reason}"}
    
    async def run(self):
        """Run the MCP server"""
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options()
            )
    async def _get_pod_metrics(self, cluster_name: str, namespace: str, 
                              pod_name: Optional[str] = None) -> dict:
        """Get pod metrics from metrics-server"""
        try:
            k8s = self._get_k8s_client(cluster_name)
            
            if 'custom_objects' not in k8s:
                api_client = k8s['core_v1'].api_client
                k8s['custom_objects'] = client.CustomObjectsApi(api_client)
            
            metrics_api = k8s['custom_objects']
            
            if pod_name and namespace != "all":
                metrics = metrics_api.get_namespaced_custom_object(
                    group="metrics.k8s.io",
                    version="v1beta1",
                    namespace=namespace,
                    plural="pods",
                    name=pod_name
                )
                items = [metrics]
            elif namespace != "all":
                metrics = metrics_api.list_namespaced_custom_object(
                    group="metrics.k8s.io",
                    version="v1beta1",
                    namespace=namespace,
                    plural="pods"
                )
                items = metrics.get('items', [])
            else:
                metrics = metrics_api.list_cluster_custom_object(
                    group="metrics.k8s.io",
                    version="v1beta1",
                    plural="pods"
                )
                items = metrics.get('items', [])
            
            pod_metrics = []
            for item in items:
                pod_data = {
                    "name": item['metadata']['name'],
                    "namespace": item['metadata']['namespace'],
                    "timestamp": item['timestamp'],
                    "containers": []
                }
                
                for container in item.get('containers', []):
                    cpu_str = container['usage']['cpu']
                    if cpu_str.endswith('n'):
                        cpu_nanocores = int(cpu_str[:-1])
                        cpu_millicores = cpu_nanocores / 1_000_000
                    elif cpu_str.endswith('u'):
                        cpu_microcores = int(cpu_str[:-1])
                        cpu_millicores = cpu_microcores / 1_000
                    elif cpu_str.endswith('m'):
                        cpu_millicores = int(cpu_str[:-1])
                    else:
                        cpu_millicores = int(cpu_str) * 1000
                    
                    memory_str = container['usage']['memory']
                    if memory_str.endswith('Ki'):
                        memory_bytes = int(memory_str[:-2]) * 1024
                    elif memory_str.endswith('Mi'):
                        memory_bytes = int(memory_str[:-2]) * 1024 * 1024
                    elif memory_str.endswith('Gi'):
                        memory_bytes = int(memory_str[:-2]) * 1024 * 1024 * 1024
                    else:
                        memory_bytes = int(memory_str)
                    
                    memory_mi = memory_bytes / (1024 * 1024)
                    
                    pod_data['containers'].append({
                        "name": container['name'],
                        "cpu": {
                            "raw": cpu_str,
                            "millicores": round(cpu_millicores, 2),
                            "cores": round(cpu_millicores / 1000, 3)
                        },
                        "memory": {
                            "raw": memory_str,
                            "bytes": memory_bytes,
                            "mebibytes": round(memory_mi, 2),
                            "gibibytes": round(memory_mi / 1024, 3)
                        }
                    })
                
                total_cpu = sum(c['cpu']['millicores'] for c in pod_data['containers'])
                total_memory = sum(c['memory']['mebibytes'] for c in pod_data['containers'])
                
                pod_data['totals'] = {
                    "cpu_millicores": round(total_cpu, 2),
                    "cpu_cores": round(total_cpu / 1000, 3),
                    "memory_mebibytes": round(total_memory, 2),
                    "memory_gibibytes": round(total_memory / 1024, 3)
                }
                
                pod_metrics.append(pod_data)
            
            return {
                "count": len(pod_metrics),
                "metrics": pod_metrics
            }
            
        except ApiException as e:
            if e.status == 404:
                return {
                    "error": "Metrics API not found. Make sure metrics-server is installed in the cluster.",
                    "details": f"{e.status} - {e.reason}"
                }
            return {"error": f"Kubernetes API error: {e.status} - {e.reason}"}
        except Exception as e:
            return {"error": f"Error fetching metrics: {str(e)}"}


def main():
        """Main entry point"""
        import argparse
        print("!!! MAIN FUNCTION STARTED !!!", file=sys.stderr, flush=True)
        
        parser = argparse.ArgumentParser(description="EKS MCP Server")
        parser.add_argument("--allow-write", action="store_true", 
                           help="Enable write operations (delete, scale, etc.)")
        parser.add_argument("--allow-sensitive-data-access", action="store_true",
                           help="Enable access to logs and events")
        
        args = parser.parse_args()
        
        print(f"Starting EKS MCP Server...", file=sys.stderr)
        print(f"Write mode: {'enabled' if args.allow_write else 'disabled'}", file=sys.stderr)
        print(f"Sensitive data access: {'enabled' if args.allow_sensitive_data_access else 'disabled'}", file=sys.stderr)
        
        server = EKSMCPServer(
            allow_write=args.allow_write,
            allow_sensitive=args.allow_sensitive_data_access
        )
        
        asyncio.run(server.run())


if __name__ == "__main__":
        main()
