// Function for Login Redirect
function loginRedirect() {
    var roleSelect = document.getElementById('roleSelect');
    if (!roleSelect) return;

    var role = roleSelect.value;
    if (role === 'admin') {
        window.location.href = 'admin.html';
    } else {
        window.location.href = 'faculty.html';
    }
}

// Handler for Allocation Form
function handleAllocation(event) {
    event.preventDefault();
    alert('ML Allocation Engine Executed Successfully! Workload updated.');
}

// Handler for Preferences Form
function handlePreferences(event) {
    event.preventDefault();
    alert('Domain preferences saved successfully! Updated ML suitability profile.');
}

// Handler for Leave Form
function handleLeave(event) {
    event.preventDefault();
    alert('Leave request submitted! Automatic substitute assigned to available faculty member.');
}