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
# Set matplotlib backend before importing pyplot to avoid GUI issues
import matplotlib
matplotlib.use('Agg')  # Use non-GUI backend
import matplotlib.pyplot as plt
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import traceback
from queue import Queue
import warnings
warnings.filterwarnings('ignore')

# Optional imports for advanced regression analysis
try:
    from scipy import stats
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    print("WARNING: scipy not available. Advanced statistical analysis will be limited.")

try:
    from sklearn.preprocessing import PolynomialFeatures
    from sklearn.linear_model import LinearRegression
    from sklearn.pipeline import Pipeline
    from sklearn.metrics import r2_score
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    print("WARNING: sklearn not available. Polynomial regression analysis will be limited.")


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
            self.base_url = "http://127.0.0.1"
            self.auth_token = None
            self.auth_credentials = {
                "email": "admin@rootly.com",
                "password": "Admin123!",
                "login_endpoint": "/api/v1/auth/login",
                "login_port": 8080
            }
            self.timeout_seconds = 30
            
            # Test progression - can be defined by range or explicit list
            self.min_users = 1
            self.max_users = 3000
            self.user_step = 50  # Increment between tests
            # Alternative: explicit list (if user_counts is defined, it takes precedence)
            # self.user_counts = [1, 5, 10, 25, 50, 100, 200, 500, 1000]
            
            self.ramp_up_period_s = 1  # Time to spawn all users
            self.requests_per_user = 1
            
            # Cooldown periods for system recovery
            self.cooldown_between_tests_s = 0  # Cooldown after each test iteration
            self.tests_per_set = 10  # Number of tests before a longer cooldown
            self.cooldown_between_sets_s = 5  # Longer cooldown after a set of tests
            
            # Auto failure point detection
            self.auto_find_failure_point = True  # Continue testing until failure threshold
            self.failure_threshold_percent = 50  # Error rate to reach
            self.max_auto_users = 10000  # Maximum users when auto-finding failure point
            
            # Threading options
            self.use_threads = True  # Use threads for concurrent requests
            self.max_workers = 500  # Maximum thread pool size
            
            # Endpoints to test
            self.endpoints = [
                {
                    "name": "data_ingestion",
                    "method": "POST",
                    "path": "/ingest",
                    "port": 8005,
                    "headers": {"Content-Type": "application/json"},
                    "body": {
                        "id_controller": "FARM-001",
                        "soil_humidity": 45.5,
                        "air_humidity": 62.3,
                        "temperature": 24.8,
                        "light_intensity": 15000.0
                    }
                },
                {
                    "name": "graphql_analytics",
                    "method": "POST",
                    "path": "/api/v1/graphql",
                    "port": 8080,
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
    
    def get_user_counts(self) -> List[int]:
        """Get the list of user counts to test"""
        # If explicit user_counts is defined, use it
        if hasattr(self, 'user_counts') and self.user_counts:
            return self.user_counts
        
        # Otherwise, generate from min_users, max_users, and user_step
        min_users = getattr(self, 'min_users', 1)
        max_users = getattr(self, 'max_users', 1000)
        user_step = getattr(self, 'user_step', 50)
        
        # Generate range: min_users, min_users+step, ..., up to max_users
        user_counts = list(range(min_users, max_users + 1, user_step))
        
        # Ensure we always test with 1 user if min_users > 1
        if min_users > 1 and 1 not in user_counts:
            user_counts.insert(0, 1)
        
        # Ensure max_users is included if not already
        if max_users not in user_counts:
            user_counts.append(max_users)
        
        return sorted(user_counts)


class PerformanceTester:
    """Main performance testing class"""
    
    def __init__(self, config: PerformanceTestConfig):
        # Ensure matplotlib is properly configured
        import matplotlib
        matplotlib.use('Agg', force=True)
        
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
        Find the knee point in the performance curve
        The knee is the last optimal point before errors start occurring (>0%)
        If no errors are found, applies performance analysis to find the optimal point
        Returns: (optimal_users, knee_score)
        """
        if len(metrics_list) < 2:
            return metrics_list[-1].num_users, 0.0
        
        users = np.array([m.num_users for m in metrics_list])
        response_times = np.array([m.avg_response_time_ms for m in metrics_list])
        p95_times = np.array([m.p95_response_time_ms for m in metrics_list])
        error_rates = np.array([m.error_rate for m in metrics_list])
        throughput = np.array([m.throughput_rps for m in metrics_list])
        
        # Primary Strategy: Find first point with errors (>0%) and go back one step
        first_error_idx = None
        for i, error_rate in enumerate(error_rates):
            if error_rate > 0.0:  # Any error rate > 0%
                first_error_idx = i
                break
        
        if first_error_idx is not None:
            # Found errors - the knee is the point just before errors start
            knee_idx = max(0, first_error_idx - 1)
            confidence = 95.0  # High confidence when based on error detection
            
            print(f"  KNEE ANALYSIS: First errors detected at {users[first_error_idx]} users ({error_rates[first_error_idx]:.2f}%)")
            print(f"  KNEE ANALYSIS: Selecting previous point at {users[knee_idx]} users as optimal")
            
            return metrics_list[knee_idx].num_users, confidence
        
        # Secondary Strategy: No errors found, analyze performance characteristics
        # Focus on finding the sweet spot before performance starts degrading
        print(f"  KNEE ANALYSIS: No errors detected, analyzing performance characteristics...")
        
        # Strategy 1: Detect efficiency breakdown (throughput per user degradation)
        efficiency_knee_idx = None
        if len(throughput) >= 3:
            efficiency = throughput / users
            
            # Find peak efficiency in early measurements
            peak_range_end = max(2, min(len(efficiency) // 2, 5))  # First half or first 5 points
            peak_efficiency = np.max(efficiency[:peak_range_end])
            peak_idx = np.argmax(efficiency[:peak_range_end])
            
            # Look for sustained efficiency drop (>20% from peak)
            for i in range(peak_idx + 1, len(efficiency)):
                if efficiency[i] < peak_efficiency * 0.8:  # 20% efficiency drop
                    # Verify this is sustained (next point also shows degradation)
                    if i < len(efficiency) - 1 and efficiency[i + 1] < peak_efficiency * 0.85:
                        efficiency_knee_idx = max(0, i - 1)  # Point before degradation
                        break
                    elif i == len(efficiency) - 1:  # Last point
                        efficiency_knee_idx = max(0, i - 1)
                        break
        
        # Strategy 2: Detect response time acceleration
        response_knee_idx = None
        if len(response_times) >= 4:
            # Calculate rate of increase in response time
            growth_rates = []
            for i in range(1, len(response_times)):
                if response_times[i-1] > 0:
                    growth_rate = (response_times[i] - response_times[i-1]) / response_times[i-1]
                    growth_rates.append(growth_rate)
                else:
                    growth_rates.append(0)
            
            # Find where growth rate becomes excessive (>25% per step)
            baseline_growth = np.mean(growth_rates[:max(1, len(growth_rates)//3)])
            
            for i, growth in enumerate(growth_rates):
                if growth > 0.25 and growth > baseline_growth * 3:  # Significant acceleration
                    response_knee_idx = i  # Point before acceleration
                    break
        
        # Strategy 3: Detect throughput plateau or decline
        throughput_knee_idx = None
        if len(throughput) >= 3:
            # Find peak throughput
            peak_throughput = np.max(throughput)
            peak_throughput_idx = np.argmax(throughput)
            
            # Look for throughput decline from peak
            for i in range(peak_throughput_idx + 1, len(throughput)):
                if throughput[i] < peak_throughput * 0.95:  # 5% decline from peak
                    throughput_knee_idx = peak_throughput_idx
                    break
        
        # Strategy 4: P95 response time threshold
        p95_knee_idx = None
        if len(p95_times) >= 3:
            baseline_p95 = np.mean(p95_times[:min(3, len(p95_times))])
            
            for i, p95 in enumerate(p95_times):
                if p95 > baseline_p95 * 2.5:  # 2.5x baseline P95
                    p95_knee_idx = max(0, i - 1)
                    break
        
        # Combine strategies - prioritize efficiency and throughput indicators
        candidates = []
        
        if efficiency_knee_idx is not None:
            candidates.append(("efficiency", efficiency_knee_idx))
            
        if throughput_knee_idx is not None:
            candidates.append(("throughput", throughput_knee_idx))
            
        if response_knee_idx is not None:
            candidates.append(("response", response_knee_idx))
            
        if p95_knee_idx is not None:
            candidates.append(("p95", p95_knee_idx))
        
        if candidates:
            # Log all candidates
            for strategy, idx in candidates:
                print(f"  KNEE ANALYSIS: {strategy} suggests {users[idx]} users")
            
            # Prioritize efficiency and throughput over response time metrics
            priority_candidates = [idx for strategy, idx in candidates if strategy in ["efficiency", "throughput"]]
            
            if priority_candidates:
                knee_idx = min(priority_candidates)  # Most conservative from high-priority strategies
                confidence = 70.0
            else:
                knee_idx = min([idx for _, idx in candidates])  # Most conservative overall
                confidence = 50.0
                
        else:
            # Fallback: Use point with best efficiency in first 60% of tests
            search_range = max(2, int(len(metrics_list) * 0.6))
            efficiency = throughput[:search_range] / users[:search_range]
            knee_idx = np.argmax(efficiency)
            confidence = 30.0
            print(f"  KNEE ANALYSIS: Fallback - using peak efficiency point")
        
        # Ensure reasonable bounds
        knee_idx = max(0, min(knee_idx, len(metrics_list) - 1))
        
        print(f"  KNEE ANALYSIS: Selected {users[knee_idx]} users (confidence: {confidence:.0f}%)")
        
        return metrics_list[knee_idx].num_users, confidence
    
    def detect_anomalies_with_regression(self, x_data: np.ndarray, y_data: np.ndarray, 
                                        data_name: str = "data") -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
        """
        Detect anomalies using regression analysis
        Returns: (predicted_values, anomaly_mask, regression_info)
        """
        regression_info = {
            'r2_score': 0.0,
            'regression_type': 'none',
            'anomaly_threshold': 0.0,
            'anomalies_detected': 0
        }
        
        if len(x_data) < 3:
            return np.zeros_like(y_data), np.zeros(len(y_data), dtype=bool), regression_info
        
        # Try different regression models
        best_predictions = None
        best_r2 = -1
        best_type = 'none'
        
        # 1. Linear regression (built-in numpy)
        try:
            # Linear fit using numpy polyfit
            linear_coeffs = np.polyfit(x_data, y_data, 1)
            linear_predictions = np.polyval(linear_coeffs, x_data)
            linear_r2 = 1 - (np.sum((y_data - linear_predictions) ** 2) / 
                             np.sum((y_data - np.mean(y_data)) ** 2))
            
            if linear_r2 > best_r2:
                best_predictions = linear_predictions
                best_r2 = linear_r2
                best_type = 'linear'
                
        except Exception as e:
            print(f"    Warning: Linear regression failed for {data_name}: {e}")
        
        # 2. Polynomial regression (degree 2) using numpy
        if len(x_data) >= 4:
            try:
                poly_coeffs = np.polyfit(x_data, y_data, 2)
                poly_predictions = np.polyval(poly_coeffs, x_data)
                poly_r2 = 1 - (np.sum((y_data - poly_predictions) ** 2) / 
                              np.sum((y_data - np.mean(y_data)) ** 2))
                
                if poly_r2 > best_r2:
                    best_predictions = poly_predictions
                    best_r2 = poly_r2
                    best_type = 'polynomial_2'
                    
            except Exception as e:
                print(f"    Warning: Polynomial regression failed for {data_name}: {e}")
        
        # 3. Advanced sklearn regression (if available)
        if SKLEARN_AVAILABLE and len(x_data) >= 5:
            try:
                X = x_data.reshape(-1, 1)
                
                # Try polynomial regression with different degrees
                for degree in [2, 3]:
                    if len(x_data) >= degree + 2:
                        poly_reg = Pipeline([
                            ('poly', PolynomialFeatures(degree=degree)),
                            ('linear', LinearRegression())
                        ])
                        poly_reg.fit(X, y_data)
                        sklearn_predictions = poly_reg.predict(X)
                        sklearn_r2 = r2_score(y_data, sklearn_predictions)
                        
                        if sklearn_r2 > best_r2:
                            best_predictions = sklearn_predictions
                            best_r2 = sklearn_r2
                            best_type = f'sklearn_poly_{degree}'
                            
            except Exception as e:
                print(f"    Warning: sklearn regression failed for {data_name}: {e}")
        
        # Use best regression or fallback to mean
        if best_predictions is None:
            best_predictions = np.full_like(y_data, np.mean(y_data))
            best_r2 = 0.0
            best_type = 'mean_fallback'
        
        # Calculate residuals and detect anomalies
        residuals = np.abs(y_data - best_predictions)
        
        # Anomaly detection using statistical thresholds
        if SCIPY_AVAILABLE:
            # Use Z-score method with scipy
            z_scores = np.abs(stats.zscore(residuals))
            anomaly_mask = z_scores > 2.0  # 2 standard deviations
            threshold = 2.0
        else:
            # Simple threshold using standard deviation
            mean_residual = np.mean(residuals)
            std_residual = np.std(residuals)
            threshold = mean_residual + 2 * std_residual
            anomaly_mask = residuals > threshold
        
        regression_info.update({
            'r2_score': best_r2,
            'regression_type': best_type,
            'anomaly_threshold': threshold,
            'anomalies_detected': np.sum(anomaly_mask)
        })
        
        return best_predictions, anomaly_mask, regression_info
    
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
        """Generate performance plots with regression analysis and anomaly detection"""
        if not metrics_list:
            return
        
        # Ensure matplotlib is using Agg backend (thread-safe, no GUI)
        import matplotlib
        matplotlib.use('Agg', force=True)
        
        # Also set it not to use threading for backend initialization
        import matplotlib.pyplot as plt
        plt.ioff()  # Turn off interactive mode
        
        users = np.array([m.num_users for m in metrics_list])
        avg_response = np.array([m.avg_response_time_ms for m in metrics_list])
        p95_response = np.array([m.p95_response_time_ms for m in metrics_list])
        p99_response = np.array([m.p99_response_time_ms for m in metrics_list])
        error_rates = np.array([m.error_rate for m in metrics_list])
        throughput = np.array([m.throughput_rps for m in metrics_list])
        
        # Perform regression analysis on key metrics
        print(f"  Performing regression analysis for anomaly detection...")
        
        response_pred, response_anomalies, response_reg_info = self.detect_anomalies_with_regression(
            users, avg_response, "average response time"
        )
        throughput_pred, throughput_anomalies, throughput_reg_info = self.detect_anomalies_with_regression(
            users, throughput, "throughput"
        )
        p95_pred, p95_anomalies, p95_reg_info = self.detect_anomalies_with_regression(
            users, p95_response, "P95 response time"
        )
        
        # Print regression analysis results
        print(f"    Response Time Regression - R²: {response_reg_info['r2_score']:.3f}, "
              f"Type: {response_reg_info['regression_type']}, "
              f"Anomalies: {response_reg_info['anomalies_detected']}")
        print(f"    Throughput Regression - R²: {throughput_reg_info['r2_score']:.3f}, "
              f"Type: {throughput_reg_info['regression_type']}, "
              f"Anomalies: {throughput_reg_info['anomalies_detected']}")
        print(f"    P95 Regression - R²: {p95_reg_info['r2_score']:.3f}, "
              f"Type: {p95_reg_info['regression_type']}, "
              f"Anomalies: {p95_reg_info['anomalies_detected']}")
        
        # Create figure with subplots
        fig, axes = plt.subplots(2, 3, figsize=(20, 12))
        fig.suptitle(f'Performance Analysis with Regression - {endpoint_name}', fontsize=16, fontweight='bold')
        
        # 1. Response Time vs Users with Regression
        ax1 = axes[0, 0]
        ax1.plot(users, avg_response, 'b-o', label='Average Response', linewidth=2, markersize=4)
        ax1.plot(users, response_pred, 'r--', label=f'Regression ({response_reg_info["regression_type"]})', linewidth=2)
        ax1.plot(users, p95_response, 'g--s', label='P95', linewidth=1.5, markersize=3)
        ax1.plot(users, p99_response, 'orange', linestyle=':', label='P99', linewidth=1.5, marker='^', markersize=3)
        
        # Highlight anomalies
        if np.any(response_anomalies):
            ax1.scatter(users[response_anomalies], avg_response[response_anomalies], 
                       c='red', s=100, marker='x', linewidth=3, label='Anomalies', zorder=5)
        
        ax1.axvline(x=knee_users, color='purple', linestyle='--', linewidth=2, label=f'Knee ({knee_users} users)')
        ax1.set_xlabel('Concurrent Users')
        ax1.set_ylabel('Response Time (ms)')
        ax1.set_title(f'Response Time Analysis (R² = {response_reg_info["r2_score"]:.3f})')
        ax1.legend(loc='best', fontsize=9)
        ax1.grid(True, alpha=0.3)
        ax1.set_xlim(left=0, right=max(users) * 1.05)
        ax1.set_ylim(bottom=0)
        
        # 2. Error Rate vs Users
        ax2 = axes[0, 1]
        ax2.plot(users, error_rates, 'r-o', linewidth=2, markersize=4, label='Error Rate')
        ax2.axvline(x=knee_users, color='purple', linestyle='--', linewidth=2, label=f'Knee ({knee_users} users)')
        ax2.axhline(y=5.0, color='red', linestyle=':', linewidth=1, label='5% threshold')
        ax2.axhline(y=1.0, color='orange', linestyle=':', linewidth=1, label='1% threshold')
        
        # Highlight first error occurrence
        first_error_idx = np.where(error_rates > 0)[0]
        if len(first_error_idx) > 0:
            first_error = first_error_idx[0]
            ax2.scatter(users[first_error], error_rates[first_error], 
                       c='red', s=150, marker='*', linewidth=2, label='First Error', zorder=5)
        
        ax2.set_xlabel('Concurrent Users')
        ax2.set_ylabel('Error Rate (%)')
        ax2.set_title('Error Rate vs Concurrent Users')
        ax2.legend(loc='best', fontsize=9)
        ax2.grid(True, alpha=0.3)
        ax2.set_xlim(left=0, right=max(users) * 1.05)
        ax2.set_ylim(bottom=0)
        
        # 3. Throughput vs Users with Regression
        ax3 = axes[0, 2]
        ax3.plot(users, throughput, 'g-o', label='Throughput', linewidth=2, markersize=4)
        ax3.plot(users, throughput_pred, 'r--', label=f'Regression ({throughput_reg_info["regression_type"]})', linewidth=2)
        
        # Highlight anomalies
        if np.any(throughput_anomalies):
            ax3.scatter(users[throughput_anomalies], throughput[throughput_anomalies], 
                       c='red', s=100, marker='x', linewidth=3, label='Anomalies', zorder=5)
        
        ax3.axvline(x=knee_users, color='purple', linestyle='--', linewidth=2, label=f'Knee ({knee_users} users)')
        ax3.set_xlabel('Concurrent Users')
        ax3.set_ylabel('Throughput (req/s)')
        ax3.set_title(f'Throughput Analysis (R² = {throughput_reg_info["r2_score"]:.3f})')
        ax3.legend(loc='best', fontsize=9)
        ax3.grid(True, alpha=0.3)
        ax3.set_xlim(left=0, right=max(users) * 1.05)
        ax3.set_ylim(bottom=0)
        
        # 4. Efficiency Analysis (Throughput/Users)
        ax4 = axes[1, 0]
        efficiency = throughput / users
        ax4.plot(users, efficiency, 'purple', marker='o', label='Efficiency (req/s per user)', linewidth=2, markersize=4)
        
        # Calculate efficiency regression
        eff_pred, eff_anomalies, eff_reg_info = self.detect_anomalies_with_regression(
            users, efficiency, "efficiency"
        )
        ax4.plot(users, eff_pred, 'r--', label=f'Efficiency Trend (R² = {eff_reg_info["r2_score"]:.3f})', linewidth=2)
        
        # Highlight efficiency anomalies
        if np.any(eff_anomalies):
            ax4.scatter(users[eff_anomalies], efficiency[eff_anomalies], 
                       c='red', s=100, marker='x', linewidth=3, label='Anomalies', zorder=5)
        
        ax4.axvline(x=knee_users, color='purple', linestyle='--', linewidth=2, label=f'Knee ({knee_users} users)')
        ax4.set_xlabel('Concurrent Users')
        ax4.set_ylabel('Efficiency (req/s per user)')
        ax4.set_title('Scaling Efficiency Analysis')
        ax4.legend(loc='best', fontsize=9)
        ax4.grid(True, alpha=0.3)
        ax4.set_xlim(left=0, right=max(users) * 1.05)
        ax4.set_ylim(bottom=0)
        
        # 5. P95 Response Time Analysis
        ax5 = axes[1, 1]
        ax5.plot(users, p95_response, 'orange', marker='s', label='P95 Response Time', linewidth=2, markersize=4)
        ax5.plot(users, p95_pred, 'r--', label=f'P95 Regression ({p95_reg_info["regression_type"]})', linewidth=2)
        
        # Highlight P95 anomalies
        if np.any(p95_anomalies):
            ax5.scatter(users[p95_anomalies], p95_response[p95_anomalies], 
                       c='red', s=100, marker='x', linewidth=3, label='Anomalies', zorder=5)
        
        ax5.axvline(x=knee_users, color='purple', linestyle='--', linewidth=2, label=f'Knee ({knee_users} users)')
        ax5.set_xlabel('Concurrent Users')
        ax5.set_ylabel('P95 Response Time (ms)')
        ax5.set_title(f'P95 Response Time Analysis (R² = {p95_reg_info["r2_score"]:.3f})')
        ax5.legend(loc='best', fontsize=9)
        ax5.grid(True, alpha=0.3)
        ax5.set_xlim(left=0, right=max(users) * 1.05)
        ax5.set_ylim(bottom=0)
        
        # 6. Combined Performance Score
        ax6 = axes[1, 2]
        
        # Calculate normalized performance score (lower is better for response time, higher is better for throughput)
        max_response = max(avg_response) if max(avg_response) > 0 else 1
        max_throughput = max(throughput) if max(throughput) > 0 else 1
        
        norm_response = avg_response / max_response  # 0-1, lower is better
        norm_throughput = throughput / max_throughput  # 0-1, higher is better
        norm_errors = error_rates / 100.0 if max(error_rates) > 0 else np.zeros_like(error_rates)  # 0-1, lower is better
        
        # Performance score: higher throughput, lower response time, lower errors
        performance_score = (norm_throughput * 0.4) + ((1 - norm_response) * 0.4) + ((1 - norm_errors) * 0.2)
        
        ax6.plot(users, performance_score * 100, 'navy', marker='d', label='Performance Score', linewidth=2, markersize=4)
        ax6.axvline(x=knee_users, color='purple', linestyle='--', linewidth=2, label=f'Knee ({knee_users} users)')
        
        # Find and highlight peak performance
        peak_score_idx = np.argmax(performance_score)
        ax6.scatter(users[peak_score_idx], performance_score[peak_score_idx] * 100, 
                   c='gold', s=150, marker='*', linewidth=2, label=f'Peak Performance ({users[peak_score_idx]} users)', zorder=5)
        
        ax6.set_xlabel('Concurrent Users')
        ax6.set_ylabel('Performance Score (%)')
        ax6.set_title('Combined Performance Score')
        ax6.legend(loc='best', fontsize=9)
        ax6.grid(True, alpha=0.3)
        ax6.set_xlim(left=0, right=max(users) * 1.05)
        ax6.set_ylim(bottom=0, top=105)
        
        plt.tight_layout()
        
        # Save plot
        timestamp = self.test_start_time.strftime('%Y%m%d_%H%M%S')
        plot_file = self.output_dir / f"{endpoint_name}_performance_{timestamp}.png"
        
        try:
            plt.savefig(plot_file, dpi=300, bbox_inches='tight')
            print(f"  Plots with regression analysis saved to: {plot_file}")
            
            # Save anomaly summary
            anomaly_summary_file = self.output_dir / f"{endpoint_name}_anomalies_{timestamp}.txt"
            with open(anomaly_summary_file, 'w') as f:
                f.write(f"Anomaly Detection Results for {endpoint_name}\n")
                f.write("="*50 + "\n\n")
                
                f.write("Regression Analysis Results:\n")
                f.write(f"Response Time: R² = {response_reg_info['r2_score']:.4f}, Type = {response_reg_info['regression_type']}\n")
                f.write(f"Throughput: R² = {throughput_reg_info['r2_score']:.4f}, Type = {throughput_reg_info['regression_type']}\n")
                f.write(f"P95 Response: R² = {p95_reg_info['r2_score']:.4f}, Type = {p95_reg_info['regression_type']}\n")
                f.write(f"Efficiency: R² = {eff_reg_info['r2_score']:.4f}, Type = {eff_reg_info['regression_type']}\n\n")
                
                f.write("Detected Anomalies:\n")
                if np.any(response_anomalies):
                    f.write(f"Response Time Anomalies at users: {users[response_anomalies].tolist()}\n")
                if np.any(throughput_anomalies):
                    f.write(f"Throughput Anomalies at users: {users[throughput_anomalies].tolist()}\n")
                if np.any(p95_anomalies):
                    f.write(f"P95 Response Anomalies at users: {users[p95_anomalies].tolist()}\n")
                if np.any(eff_anomalies):
                    f.write(f"Efficiency Anomalies at users: {users[eff_anomalies].tolist()}\n")
                
                if not any([np.any(response_anomalies), np.any(throughput_anomalies), 
                           np.any(p95_anomalies), np.any(eff_anomalies)]):
                    f.write("No significant anomalies detected.\n")
                
                f.write(f"\nPeak Performance: {users[peak_score_idx]} users (Score: {performance_score[peak_score_idx]*100:.2f}%)\n")
                f.write(f"Knee Point: {knee_users} users\n")
            
            print(f"  Anomaly analysis saved to: {anomaly_summary_file}")
            
        except Exception as e:
            print(f"  WARNING: Could not save plot: {e}")
        finally:
            # Properly close the figure to free memory
            plt.close(fig)
            plt.close('all')  # Close any remaining figures
    
    async def test_endpoint(self, endpoint: Dict[str, Any]):
        """Test a single endpoint across all user counts"""
        endpoint_name = endpoint['name']
        print(f"\n{'='*60}")
        print(f"Testing Endpoint: {endpoint_name}")
        print(f"{'='*60}")
        
        # Get user counts to test
        user_counts = self.config.get_user_counts()
        print(f"  Testing with user counts: {user_counts}")
        print(f"  Total test iterations: {len(user_counts)}")
        
        # Get cooldown configuration
        cooldown_between_tests = getattr(self.config, 'cooldown_between_tests_s', 0)
        tests_per_set = getattr(self.config, 'tests_per_set', 0)
        cooldown_between_sets = getattr(self.config, 'cooldown_between_sets_s', 0)
        
        # Get auto failure point configuration
        auto_find_failure = getattr(self.config, 'auto_find_failure_point', False)
        failure_threshold = getattr(self.config, 'failure_threshold_percent', 50)
        max_auto_users = getattr(self.config, 'max_auto_users', 10000)
        user_step = getattr(self.config, 'user_step', 50)
        
        if cooldown_between_tests > 0:
            print(f"  Cooldown between tests: {cooldown_between_tests}s")
        if tests_per_set > 0 and cooldown_between_sets > 0:
            print(f"  After {tests_per_set} tests, cooldown: {cooldown_between_sets}s")
        if auto_find_failure:
            print(f"  Auto find failure point: ON (threshold: {failure_threshold}%, max users: {max_auto_users})")
        print()
        
        metrics_list = []
        
        for test_index, num_users in enumerate(user_counts, start=1):
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
            
            # Apply cooldown if not the last test
            if test_index < len(user_counts):
                # Check if we need a set cooldown
                if tests_per_set > 0 and cooldown_between_sets > 0 and test_index % tests_per_set == 0:
                    print(f"    Set cooldown: waiting {cooldown_between_sets}s for system recovery...")
                    await asyncio.sleep(cooldown_between_sets)
                # Otherwise apply regular cooldown
                elif cooldown_between_tests > 0:
                    print(f"    Cooldown: waiting {cooldown_between_tests}s...")
                    await asyncio.sleep(cooldown_between_tests)
        
        # Check if we need to auto-find failure point
        if auto_find_failure:
            max_error_rate = max([m.error_rate for m in metrics_list]) if metrics_list else 0
            
            if max_error_rate < failure_threshold:
                print(f"\n  Auto-finding failure point (current max error rate: {max_error_rate:.2f}%)")
                print(f"  Target: {failure_threshold}% error rate")
                print(f"  Continuing tests beyond max_users with step: {user_step}")
                print()
                
                # Continue testing with incremental users
                current_users = user_counts[-1] if user_counts else user_step
                test_index = len(user_counts)
                
                while max_error_rate < failure_threshold and current_users < max_auto_users:
                    current_users += user_step
                    test_index += 1
                    
                    # Apply cooldown before test
                    if tests_per_set > 0 and cooldown_between_sets > 0 and test_index % tests_per_set == 0:
                        print(f"    Set cooldown: waiting {cooldown_between_sets}s for system recovery...")
                        await asyncio.sleep(cooldown_between_sets)
                    elif cooldown_between_tests > 0:
                        print(f"    Cooldown: waiting {cooldown_between_tests}s...")
                        await asyncio.sleep(cooldown_between_tests)
                    
                    test_start = time.time()
                    results = await self.run_load_test(endpoint, current_users)
                    test_duration = time.time() - test_start
                    
                    metrics = self.calculate_metrics(results, current_users, test_duration)
                    metrics_list.append(metrics)
                    
                    # Print summary
                    print(f"    Users: {current_users:4d} | "
                          f"Avg: {metrics.avg_response_time_ms:7.2f}ms | "
                          f"P95: {metrics.p95_response_time_ms:7.2f}ms | "
                          f"Errors: {metrics.error_rate:5.2f}% | "
                          f"Throughput: {metrics.throughput_rps:6.2f} req/s")
                    
                    max_error_rate = metrics.error_rate
                    
                    # Check if we reached the threshold
                    if max_error_rate >= failure_threshold:
                        print(f"\n  FAILURE THRESHOLD REACHED at {current_users} users ({max_error_rate:.2f}% error rate)")
                        break
                
                if current_users >= max_auto_users:
                    print(f"\n  Maximum auto users limit reached ({max_auto_users})")
                    print(f"  Final error rate: {max_error_rate:.2f}%")
            else:
                print(f"\n  Failure threshold already reached in initial tests (max error: {max_error_rate:.2f}%)")
        
        # Find knee
        knee_users, knee_score = self.find_knee(metrics_list)
        print(f"\n  KNEE DETECTED at {knee_users} concurrent users")
        
        # Save results
        self.save_results_csv(metrics_list, endpoint_name)
        
        # Generate plots with error handling
        try:
            self.generate_plots(metrics_list, endpoint_name, knee_users)
        except Exception as e:
            print(f"  WARNING: Could not generate plots for {endpoint_name}: {e}")
            print(f"  CSV data is still available for manual analysis")
        
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


def setup_matplotlib():
    """Configure matplotlib for headless operation"""
    import matplotlib
    # Force Agg backend to prevent GUI issues
    matplotlib.use('Agg', force=True)
    
    # Disable interactive mode
    import matplotlib.pyplot as plt
    plt.ioff()
    
    # Set thread safety
    import os
    os.environ['MPLBACKEND'] = 'Agg'


def main():
    """Main entry point"""
    # Configure matplotlib for headless operation
    setup_matplotlib()
    
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
