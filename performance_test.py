import asyncio
import aiohttp
import time
import json
import csv
import statistics
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Tuple
import numpy as np
from dataclasses import dataclass, asdict
import matplotlib.pyplot as plt
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import traceback
from queue import Queue


@dataclass
class RequestResult:
    """Result of a single HTTP request"""
    timestamp: float
    elapsed_ms: float
    status_code: int
    success: bool
    error_message: str = ""


@dataclass
class TestMetrics:
    """Metrics for a specific load level"""
    num_users: int
    total_requests: int
    successful_requests: int
    failed_requests: int
    error_rate: float
    avg_response_time_ms: float
    min_response_time_ms: float
    max_response_time_ms: float
    median_response_time_ms: float
    p90_response_time_ms: float
    p95_response_time_ms: float
    p99_response_time_ms: float
    std_dev_ms: float
    throughput_rps: float  # requests per second
    total_duration_s: float
    turnaround_time_ms: float  # Time from first to last request
    received_kb_sec: float
    sent_kb_sec: float


class PerformanceTestConfig:
    """Configuration for performance tests"""
    
    def __init__(self, config_file: str = None):
        if config_file and Path(config_file).exists():
            with open(config_file, 'r') as f:
                config_data = json.load(f)
                self.__dict__.update(config_data)
        else:
            # Default configuration
            self.base_url = "http://192.168.1.100"  # Machine A IP
            self.auth_token = None
            self.auth_credentials = {
                "email": "Admin@rootly.com",
                "password": "Admin123!",
                "login_endpoint": "/api/auth/login",
                "login_port": 8000
            }
            self.timeout_seconds = 30
            
            # Test progression
            self.user_counts = [1, 5, 10, 25, 50, 100, 200, 500, 1000, 1500, 2000]
            self.ramp_up_period_s = 1  # Time to spawn all users
            self.requests_per_user = 1
            
            # Threading options
            self.use_threads = True  # Use threads for concurrent requests
            self.max_workers = 500  # Maximum thread pool size
            
            # Endpoints to test
            self.endpoints = [
                {
                    "name": "data_ingestion",
                    "method": "POST",
                    "path": "/measurements",
                    "port": 8080,
                    "headers": {"Content-Type": "application/json"},
                    "body": {
                        "deviceId": "sensor-001",
                        "temperature": 25.5,
                        "humidity": 60.0,
                        "soilMoisture": 45.0,
                        "timestamp": None  # Will be set dynamically
                    }
                },
                {
                    "name": "graphql_analytics",
                    "method": "POST",
                    "path": "/api/v1/graphql",
                    "port": 8081,
                    "headers": {"Content-Type": "application/json"},
                    "body": {
                        "query": """
                            query GetPlantMetrics($plantId: ID!) {
                                plant(id: $plantId) {
                                    id
                                    name
                                    metrics {
                                        temperature
                                        humidity
                                        soilMoisture
                                    }
                                }
                            }
                        """,
                        "variables": {"plantId": "1"}
                    }
                }
            ]
    
    def save_config(self, filepath: str):
        """Save current configuration to file"""
        with open(filepath, 'w') as f:
            json.dump(self.__dict__, f, indent=2)


class PerformanceTester:
    """Main performance testing class"""
    
    def __init__(self, config: PerformanceTestConfig):
        self.config = config
        self.test_start_time = datetime.now()
        self.output_dir = Path(f"results_{self.test_start_time.strftime('%Y%m%d_%H%M%S')}")
        self.output_dir.mkdir(exist_ok=True)
        
        # Save configuration
        config.save_config(str(self.output_dir / "config.json"))
    
    async def obtain_auth_token(self) -> bool:
        """Obtain authentication token from API Gateway"""
        if not hasattr(self.config, 'auth_credentials'):
            print("  WARNING: No auth credentials configured, skipping authentication")
            return True
        
        auth_creds = self.config.auth_credentials
        login_url = f"{self.config.base_url}:{auth_creds['login_port']}{auth_creds['login_endpoint']}"
        
        print(f"\n{'='*60}")
        print(f"Obtaining Authentication Token")
        print(f"{'='*60}")
        print(f"  Login URL: {login_url}")
        print(f"  Email: {auth_creds['email']}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    login_url,
                    json={
                        "email": auth_creds['email'],
                        "password": auth_creds['password']
                    },
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        # Try different possible token field names
                        token = data.get('access_token') or data.get('token') or data.get('accessToken')
                        
                        if token:
                            self.config.auth_token = token
                            print(f"  SUCCESS: Token obtained")
                            print(f"  Token (first 20 chars): {token[:20]}...")
                            return True
                        else:
                            print(f"  ERROR: Token not found in response")
                            print(f"  Response data: {data}")
                            return False
                    else:
                        error_text = await response.text()
                        print(f"  ERROR: Authentication failed with status {response.status}")
                        print(f"  Response: {error_text}")
                        return False
        
        except Exception as e:
            print(f"  ERROR: Exception during authentication: {e}")
            traceback.print_exc()
            return False
    
    async def make_request(
        self, 
        session: aiohttp.ClientSession, 
        endpoint: Dict[str, Any]
    ) -> RequestResult:
        """Make a single HTTP request"""
        url = f"{self.config.base_url}:{endpoint['port']}{endpoint['path']}"
        method = endpoint['method'].upper()
        headers = endpoint.get('headers', {}).copy()
        
        # Add auth token if available
        if self.config.auth_token:
            headers['Authorization'] = f"Bearer {self.config.auth_token}"
        
        # Prepare body
        body = endpoint.get('body')
        if body and isinstance(body, dict):
            # Update timestamp for data ingestion
            if 'timestamp' in body and body['timestamp'] is None:
                body = body.copy()
                body['timestamp'] = int(time.time() * 1000)
        
        start_time = time.time()
        
        try:
            async with session.request(
                method, 
                url, 
                json=body if body else None,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds)
            ) as response:
                await response.read()  # Consume response
                elapsed = (time.time() - start_time) * 1000  # Convert to ms
                
                return RequestResult(
                    timestamp=start_time,
                    elapsed_ms=elapsed,
                    status_code=response.status,
                    success=(200 <= response.status < 300),
                    error_message=""
                )
        
        except asyncio.TimeoutError:
            elapsed = (time.time() - start_time) * 1000
            return RequestResult(
                timestamp=start_time,
                elapsed_ms=elapsed,
                status_code=0,
                success=False,
                error_message="Timeout"
            )
        
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            return RequestResult(
                timestamp=start_time,
                elapsed_ms=elapsed,
                status_code=0,
                success=False,
                error_message=str(e)
            )
    
    def make_request_sync(self, endpoint: Dict[str, Any]) -> RequestResult:
        """Make a single HTTP request synchronously (for threading)"""
        import requests
        
        url = f"{self.config.base_url}:{endpoint['port']}{endpoint['path']}"
        method = endpoint['method'].upper()
        headers = endpoint.get('headers', {}).copy()
        
        # Add auth token if available
        if self.config.auth_token:
            headers['Authorization'] = f"Bearer {self.config.auth_token}"
        
        # Prepare body
        body = endpoint.get('body')
        if body and isinstance(body, dict):
            # Update timestamp for data ingestion
            if 'timestamp' in body and body['timestamp'] is None:
                body = body.copy()
                body['timestamp'] = int(time.time() * 1000)
        
        start_time = time.time()
        
        try:
            response = requests.request(
                method,
                url,
                json=body if body else None,
                headers=headers,
                timeout=self.config.timeout_seconds
            )
            
            elapsed = (time.time() - start_time) * 1000  # Convert to ms
            
            return RequestResult(
                timestamp=start_time,
                elapsed_ms=elapsed,
                status_code=response.status_code,
                success=(200 <= response.status_code < 300),
                error_message=""
            )
        
        except requests.exceptions.Timeout:
            elapsed = (time.time() - start_time) * 1000
            return RequestResult(
                timestamp=start_time,
                elapsed_ms=elapsed,
                status_code=0,
                success=False,
                error_message="Timeout"
            )
        
        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            return RequestResult(
                timestamp=start_time,
                elapsed_ms=elapsed,
                status_code=0,
                success=False,
                error_message=str(e)
            )
    
    def run_user_session_sync(
        self,
        endpoint: Dict[str, Any],
        user_id: int,
        spawn_time: float,
        results_queue: Queue
    ):
        """Simulate a single user session synchronously (for threading)"""
        # Wait until scheduled spawn time
        current_time = time.time()
        if spawn_time > current_time:
            time.sleep(spawn_time - current_time)
        
        results = []
        for _ in range(self.config.requests_per_user):
            result = self.make_request_sync(endpoint)
            results.append(result)
        
        # Put results in queue
        for result in results:
            results_queue.put(result)
    
    async def run_user_session(
        self, 
        session: aiohttp.ClientSession, 
        endpoint: Dict[str, Any],
        user_id: int,
        delay: float
    ) -> List[RequestResult]:
        """Simulate a single user session"""
        # Wait for ramp-up delay
        await asyncio.sleep(delay)
        
        results = []
        for _ in range(self.config.requests_per_user):
            result = await self.make_request(session, endpoint)
            results.append(result)
        
        return results
    
    def run_load_test_with_threads(
        self,
        endpoint: Dict[str, Any],
        num_users: int
    ) -> List[RequestResult]:
        """Run a load test using threads with proper ramp-up"""
        print(f"  Testing with {num_users} concurrent users (Threading mode)...")
        
        # Calculate delay between user spawns to achieve ramp-up
        delay_between_users = self.config.ramp_up_period_s / num_users if num_users > 1 else 0
        
        # Queue to collect results from all threads
        results_queue = Queue()
        
        # Calculate spawn times for each user
        test_start_time = time.time()
        
        # Use ThreadPoolExecutor for managing threads
        max_workers = min(num_users, self.config.max_workers)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            
            for user_id in range(num_users):
                # Calculate when this user should start
                spawn_time = test_start_time + (user_id * delay_between_users)
                
                # Submit the user session to the thread pool
                future = executor.submit(
                    self.run_user_session_sync,
                    endpoint,
                    user_id,
                    spawn_time,
                    results_queue
                )
                futures.append(future)
            
            # Wait for all threads to complete
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"    Thread error: {e}")
        
        # Collect all results from queue
        all_results = []
        while not results_queue.empty():
            all_results.append(results_queue.get())
        
        return all_results
    
    async def run_load_test(
        self, 
        endpoint: Dict[str, Any], 
        num_users: int
    ) -> List[RequestResult]:
        """Run a load test with specified number of users"""
        # Choose between threading or async based on configuration
        if hasattr(self.config, 'use_threads') and self.config.use_threads:
            # Use threading approach (synchronous)
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                self.run_load_test_with_threads,
                endpoint,
                num_users
            )
        else:
            # Use async approach (original)
            print(f"  Testing with {num_users} concurrent users (Async mode)...")
            
            # Calculate delay between user spawns
            delay_between_users = self.config.ramp_up_period_s / num_users if num_users > 0 else 0
            
            connector = aiohttp.TCPConnector(limit=num_users + 50)  # Connection pool
            timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
            
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                tasks = []
                for user_id in range(num_users):
                    delay = user_id * delay_between_users
                    task = self.run_user_session(session, endpoint, user_id, delay)
                    tasks.append(task)
                
                # Wait for all users to complete
                user_results = await asyncio.gather(*tasks)
                
                # Flatten results
                all_results = [result for user_result in user_results for result in user_result]
            
            return all_results
    
    def calculate_metrics(
        self, 
        results: List[RequestResult], 
        num_users: int,
        test_duration: float
    ) -> TestMetrics:
        """Calculate performance metrics from results"""
        total_requests = len(results)
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        
        successful_requests = len(successful)
        failed_requests = len(failed)
        error_rate = (failed_requests / total_requests * 100) if total_requests > 0 else 0
        
        if successful_requests > 0:
            response_times = [r.elapsed_ms for r in successful]
            avg_response = statistics.mean(response_times)
            min_response = min(response_times)
            max_response = max(response_times)
            median_response = statistics.median(response_times)
            std_dev = statistics.stdev(response_times) if len(response_times) > 1 else 0
            
            # Percentiles
            sorted_times = sorted(response_times)
            p90 = np.percentile(sorted_times, 90)
            p95 = np.percentile(sorted_times, 95)
            p99 = np.percentile(sorted_times, 99)
        else:
            avg_response = min_response = max_response = median_response = 0
            std_dev = p90 = p95 = p99 = 0
        
        # Throughput
        throughput = successful_requests / test_duration if test_duration > 0 else 0
        
        # Turnaround time (time from first to last request)
        if results:
            timestamps = [r.timestamp for r in results]
            turnaround_time = (max(timestamps) - min(timestamps)) * 1000  # ms
        else:
            turnaround_time = 0
        
        # Approximate data transfer (simplified)
        # Assuming ~500 bytes per request/response
        avg_request_size_kb = 0.5
        avg_response_size_kb = 2.0
        
        sent_kb_sec = (successful_requests * avg_request_size_kb) / test_duration if test_duration > 0 else 0
        received_kb_sec = (successful_requests * avg_response_size_kb) / test_duration if test_duration > 0 else 0
        
        return TestMetrics(
            num_users=num_users,
            total_requests=total_requests,
            successful_requests=successful_requests,
            failed_requests=failed_requests,
            error_rate=error_rate,
            avg_response_time_ms=avg_response,
            min_response_time_ms=min_response,
            max_response_time_ms=max_response,
            median_response_time_ms=median_response,
            p90_response_time_ms=p90,
            p95_response_time_ms=p95,
            p99_response_time_ms=p99,
            std_dev_ms=std_dev,
            throughput_rps=throughput,
            total_duration_s=test_duration,
            turnaround_time_ms=turnaround_time,
            received_kb_sec=received_kb_sec,
            sent_kb_sec=sent_kb_sec
        )
    
    def find_knee(self, metrics_list: List[TestMetrics]) -> Tuple[int, float]:
        """
        Find the knee point in the performance curve using the "elbow method"
        Returns: (optimal_users, knee_score)
        """
        if len(metrics_list) < 3:
            return metrics_list[-1].num_users, 0.0
        
        users = np.array([m.num_users for m in metrics_list])
        response_times = np.array([m.avg_response_time_ms for m in metrics_list])
        error_rates = np.array([m.error_rate for m in metrics_list])
        
        # Normalize data
        users_norm = (users - users.min()) / (users.max() - users.min())
        response_norm = (response_times - response_times.min()) / (response_times.max() - response_times.min() + 1e-10)
        
        # Calculate distance from ideal point (0 users, 0 response time)
        # Using weighted combination of response time and error rate
        scores = response_norm + (error_rates / 100) * 2  # Weight errors more
        
        # Find point with maximum curvature
        # Using second derivative approximation
        if len(scores) >= 3:
            curvature = np.zeros(len(scores))
            for i in range(1, len(scores) - 1):
                curvature[i] = abs(scores[i-1] - 2*scores[i] + scores[i+1])
            
            knee_idx = np.argmax(curvature)
        else:
            # Fallback: find where error rate starts increasing significantly
            knee_idx = 0
            for i, m in enumerate(metrics_list):
                if m.error_rate > 5.0:  # 5% error threshold
                    knee_idx = max(0, i - 1)
                    break
            else:
                knee_idx = len(metrics_list) - 1
        
        return metrics_list[knee_idx].num_users, scores[knee_idx]
    
    def save_results_csv(self, metrics_list: List[TestMetrics], endpoint_name: str):
        """Save metrics to CSV file"""
        timestamp = self.test_start_time.strftime('%Y%m%d_%H%M%S')
        csv_file = self.output_dir / f"{endpoint_name}_metrics_{timestamp}.csv"
        
        with open(csv_file, 'w', newline='') as f:
            if metrics_list:
                writer = csv.DictWriter(f, fieldnames=asdict(metrics_list[0]).keys())
                writer.writeheader()
                for metrics in metrics_list:
                    writer.writerow(asdict(metrics))
        
        print(f"  Results saved to: {csv_file}")
    
    def generate_plots(self, metrics_list: List[TestMetrics], endpoint_name: str, knee_users: int):
        """Generate performance plots"""
        if not metrics_list:
            return
        
        users = [m.num_users for m in metrics_list]
        avg_response = [m.avg_response_time_ms for m in metrics_list]
        p95_response = [m.p95_response_time_ms for m in metrics_list]
        p99_response = [m.p99_response_time_ms for m in metrics_list]
        error_rates = [m.error_rate for m in metrics_list]
        throughput = [m.throughput_rps for m in metrics_list]
        
        # Create figure with subplots
        fig, axes = plt.subplots(2, 2, figsize=(15, 10))
        fig.suptitle(f'Performance Analysis - {endpoint_name}', fontsize=16, fontweight='bold')
        
        # 1. Response Time vs Users
        ax1 = axes[0, 0]
        ax1.plot(users, avg_response, 'b-o', label='Average', linewidth=2)
        ax1.plot(users, p95_response, 'g--s', label='P95', linewidth=1.5)
        ax1.plot(users, p99_response, 'r--^', label='P99', linewidth=1.5)
        ax1.axvline(x=knee_users, color='orange', linestyle='--', linewidth=2, label=f'Knee ({knee_users} users)')
        ax1.set_xlabel('Concurrent Users')
        ax1.set_ylabel('Response Time (ms)')
        ax1.set_title('Response Time vs Concurrent Users')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # 2. Error Rate vs Users
        ax2 = axes[0, 1]
        ax2.plot(users, error_rates, 'r-o', linewidth=2)
        ax2.axvline(x=knee_users, color='orange', linestyle='--', linewidth=2, label=f'Knee ({knee_users} users)')
        ax2.axhline(y=5.0, color='red', linestyle=':', linewidth=1, label='5% threshold')
        ax2.set_xlabel('Concurrent Users')
        ax2.set_ylabel('Error Rate (%)')
        ax2.set_title('Error Rate vs Concurrent Users')
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        
        # 3. Throughput vs Users
        ax3 = axes[1, 0]
        ax3.plot(users, throughput, 'g-o', linewidth=2)
        ax3.axvline(x=knee_users, color='orange', linestyle='--', linewidth=2, label=f'Knee ({knee_users} users)')
        ax3.set_xlabel('Concurrent Users')
        ax3.set_ylabel('Throughput (req/s)')
        ax3.set_title('Throughput vs Concurrent Users')
        ax3.legend()
        ax3.grid(True, alpha=0.3)
        
        # 4. Performance Curve (Combined)
        ax4 = axes[1, 1]
        
        # Normalize for comparison
        max_response = max(avg_response) if max(avg_response) > 0 else 1
        max_throughput = max(throughput) if max(throughput) > 0 else 1
        
        norm_response = [r / max_response * 100 for r in avg_response]
        norm_throughput = [t / max_throughput * 100 for t in throughput]
        
        ax4.plot(users, norm_response, 'b-o', label='Response Time (normalized)', linewidth=2)
        ax4.plot(users, norm_throughput, 'g-s', label='Throughput (normalized)', linewidth=2)
        ax4.axvline(x=knee_users, color='orange', linestyle='--', linewidth=2, label=f'Knee ({knee_users} users)')
        ax4.set_xlabel('Concurrent Users')
        ax4.set_ylabel('Normalized Value (%)')
        ax4.set_title('Performance Curve (Normalized)')
        ax4.legend()
        ax4.grid(True, alpha=0.3)
        
        plt.tight_layout()
        
        # Save plot
        timestamp = self.test_start_time.strftime('%Y%m%d_%H%M%S')
        plot_file = self.output_dir / f"{endpoint_name}_performance_{timestamp}.png"
        plt.savefig(plot_file, dpi=300, bbox_inches='tight')
        plt.close()
        
        print(f"  Plots saved to: {plot_file}")
    
    async def test_endpoint(self, endpoint: Dict[str, Any]):
        """Test a single endpoint across all user counts"""
        endpoint_name = endpoint['name']
        print(f"\n{'='*60}")
        print(f"Testing Endpoint: {endpoint_name}")
        print(f"{'='*60}")
        
        metrics_list = []
        
        for num_users in self.config.user_counts:
            test_start = time.time()
            
            results = await self.run_load_test(endpoint, num_users)
            
            test_duration = time.time() - test_start
            
            metrics = self.calculate_metrics(results, num_users, test_duration)
            metrics_list.append(metrics)
            
            # Print summary
            print(f"    Users: {num_users:4d} | "
                  f"Avg: {metrics.avg_response_time_ms:7.2f}ms | "
                  f"P95: {metrics.p95_response_time_ms:7.2f}ms | "
                  f"Errors: {metrics.error_rate:5.2f}% | "
                  f"Throughput: {metrics.throughput_rps:6.2f} req/s")
            
            # Stop if error rate is too high
            if metrics.error_rate > 50:
                print(f"  WARNING: Stopping test - error rate exceeded 50%")
                break
        
        # Find knee
        knee_users, knee_score = self.find_knee(metrics_list)
        print(f"\n  KNEE DETECTED at {knee_users} concurrent users")
        
        # Save results
        self.save_results_csv(metrics_list, endpoint_name)
        self.generate_plots(metrics_list, endpoint_name, knee_users)
        
        return metrics_list, knee_users
    
    async def run_all_tests(self):
        """Run tests for all configured endpoints"""
        print(f"\n{'#'*60}")
        print(f"# Performance Testing Started")
        print(f"# Output Directory: {self.output_dir}")
        print(f"# Timestamp: {self.test_start_time}")
        print(f"{'#'*60}")
        
        # Obtain authentication token first
        auth_success = await self.obtain_auth_token()
        if not auth_success:
            print("\nWARNING: Could not obtain authentication token.")
            print("Tests will proceed but may fail if authentication is required.")
            user_input = input("Continue anyway? (y/n): ")
            if user_input.lower() != 'y':
                print("Tests aborted by user.")
                return
        
        all_results = {}
        
        for endpoint in self.config.endpoints:
            try:
                metrics_list, knee_users = await self.test_endpoint(endpoint)
                all_results[endpoint['name']] = {
                    'metrics': metrics_list,
                    'knee_users': knee_users
                }
            except Exception as e:
                print(f"  ERROR testing {endpoint['name']}: {e}")
                import traceback
                traceback.print_exc()
        
        # Generate summary report
        self.generate_summary_report(all_results)
        
        print(f"\n{'#'*60}")
        print(f"# Performance Testing Completed")
        print(f"# Results saved in: {self.output_dir}")
        print(f"{'#'*60}\n")
    
    def generate_summary_report(self, all_results: Dict[str, Any]):
        """Generate a summary report of all tests"""
        summary_file = self.output_dir / "summary_report.txt"
        
        with open(summary_file, 'w') as f:
            f.write("="*70 + "\n")
            f.write("PERFORMANCE TEST SUMMARY REPORT\n")
            f.write("="*70 + "\n")
            f.write(f"Test Date: {self.test_start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Base URL: {self.config.base_url}\n")
            f.write("\n")
            
            for endpoint_name, data in all_results.items():
                metrics_list = data['metrics']
                knee_users = data['knee_users']
                
                f.write("-"*70 + "\n")
                f.write(f"Endpoint: {endpoint_name}\n")
                f.write("-"*70 + "\n")
                f.write(f"Knee Point: {knee_users} concurrent users\n")
                
                if metrics_list:
                    best_metrics = metrics_list[0]
                    knee_metrics = next((m for m in metrics_list if m.num_users == knee_users), metrics_list[-1])
                    last_metrics = metrics_list[-1]
                    
                    f.write(f"\nBest Performance (1 user):\n")
                    f.write(f"  Avg Response Time: {best_metrics.avg_response_time_ms:.2f} ms\n")
                    f.write(f"  Throughput: {best_metrics.throughput_rps:.2f} req/s\n")
                    
                    f.write(f"\nKnee Performance ({knee_users} users):\n")
                    f.write(f"  Avg Response Time: {knee_metrics.avg_response_time_ms:.2f} ms\n")
                    f.write(f"  P95 Response Time: {knee_metrics.p95_response_time_ms:.2f} ms\n")
                    f.write(f"  P99 Response Time: {knee_metrics.p99_response_time_ms:.2f} ms\n")
                    f.write(f"  Error Rate: {knee_metrics.error_rate:.2f}%\n")
                    f.write(f"  Throughput: {knee_metrics.throughput_rps:.2f} req/s\n")
                    
                    f.write(f"\nMax Load Tested ({last_metrics.num_users} users):\n")
                    f.write(f"  Avg Response Time: {last_metrics.avg_response_time_ms:.2f} ms\n")
                    f.write(f"  Error Rate: {last_metrics.error_rate:.2f}%\n")
                    f.write(f"  Throughput: {last_metrics.throughput_rps:.2f} req/s\n")
                
                f.write("\n")
        
        print(f"\n  Summary report saved to: {summary_file}")


def main():
    """Main entry point"""
    # Check for config file argument
    config_file = sys.argv[1] if len(sys.argv) > 1 else None
    
    # Load configuration
    config = PerformanceTestConfig(config_file)
    
    # Create tester
    tester = PerformanceTester(config)
    
    # Run tests
    asyncio.run(tester.run_all_tests())


if __name__ == "__main__":
    main()
