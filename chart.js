document.addEventListener('DOMContentLoaded', function() {
    const chartCanvas = document.getElementById('workloadChart');
    if (!chartCanvas) return;

    const ctx = chartCanvas.getContext('2d');
    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: ['Dr. M. K. Sharma', 'Prof. R. V. Rao', 'Dr. S. K. Gupta', 'Prof. A. N. Reddy', 'Dr. P. C. Mehta'],
            datasets: [{
                label: 'Assigned Weekly Contact Hours',
                data: [12, 14, 13, 11, 14],
                backgroundColor: ['#0d6efd', '#198754', '#0dcaf0', '#ffc107', '#6c757d'],
                borderRadius: 5
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: true, position: 'top' }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    max: 20,
                    title: { display: true, text: 'Teaching Hours / Week' }
                }
            }
        }
    });
});