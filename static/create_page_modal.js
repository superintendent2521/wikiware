document.addEventListener('DOMContentLoaded', function() {
    // Create modal for new page creation
    const modalHTML = `
        <div id="createPageModal" class="modal">
            <div class="modal-content">
                <span class="close">&times;</span>
                <h3>Create New Page</h3>
                <form id="createPageForm">
                    <div class="form-group">
                        <label for="pageTitle">Page Title:</label>
                        <input type="text" id="pageTitle" name="pageTitle" required placeholder="Enter page title">
                    </div>
                    <div class="form-actions">
                        <button type="submit" class="btn btn-primary">Create Page</button>
                        <button type="button" class="btn btn-secondary" id="cancelCreate">Cancel</button>
                    </div>
                </form>
            </div>
        </div>
    `;
    
    // Add modal to body
    document.body.insertAdjacentHTML('beforeend', modalHTML);
    
    // Get modal elements
    const modal = document.getElementById('createPageModal');
    const closeBtn = modal.querySelector('.close');
    const cancelBtn = document.getElementById('cancelCreate');
    const form = document.getElementById('createPageForm');
    const pageTitleInput = document.getElementById('pageTitle');
    
    // Hide modal initially
    modal.style.display = 'none';
    
    // Show modal when create button is clicked
    const createBtns = document.querySelectorAll('.create-page-btn');
    createBtns.forEach(function(createBtn) {
        createBtn.addEventListener('click', function(e) {
            e.preventDefault();
            modal.style.display = 'block';
            // Focus on title input
            pageTitleInput.focus();
            // Set branch if available
            const branch = this.getAttribute('data-branch') || 'main';
            // Could store branch in a hidden input or something if needed
        });
    });
    
    // Close modal when close button is clicked
    closeBtn.addEventListener('click', function() {
        modal.style.display = 'none';
    });
    
    // Close modal when cancel button is clicked
    cancelBtn.addEventListener('click', function() {
        modal.style.display = 'none';
    });
    
    // Close modal when clicking outside of modal content
    window.addEventListener('click', function(event) {
        if (event.target === modal) {
            modal.style.display = 'none';
        }
    });
    
    // Handle form submission
    form.addEventListener('submit', function(e) {
        e.preventDefault();
        
        const pageTitle = pageTitleInput.value.trim();
        if (!pageTitle) {
            return;
        }
        
        // Redirect to edit page with the new title
        window.location.href = `/edit/${encodeURIComponent(pageTitle)}?branch=main`;
        });
});
