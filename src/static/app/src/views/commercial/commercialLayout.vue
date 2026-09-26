<template>
  <div class="container-fluid pb-4">
    <div class="d-flex align-items-start gap-3 mb-4 flex-wrap">
      <div>
        <h2 class="mb-1">Commercial Panel</h2>
        <p class="text-muted mb-0">Nodes, locations, packages and customer subscriptions</p>
      </div>
      <nav class="nav nav-pills ms-lg-auto commercial-nav">
        <RouterLink class="nav-link" to="/commercial/users"><i class="bi bi-people me-2"></i>Users</RouterLink>
        <RouterLink class="nav-link" to="/commercial/nodes"><i class="bi bi-hdd-rack me-2"></i>Nodes</RouterLink>
        <RouterLink class="nav-link" to="/commercial/node-groups"><i class="bi bi-geo-alt me-2"></i>Node Groups</RouterLink>
        <RouterLink class="nav-link" to="/commercial/outbounds"><i class="bi bi-diagram-3 me-2"></i>Outbounds</RouterLink>
        <RouterLink class="nav-link" to="/commercial/packages"><i class="bi bi-box-seam me-2"></i>Packages</RouterLink>
        <RouterLink class="nav-link" to="/commercial/subscriptions"><i class="bi bi-credit-card me-2"></i>Subscriptions</RouterLink>
      </nav>
    </div>
    <RouterView />
  </div>
</template>

<style scoped>
.commercial-nav {
  gap: .35rem;
}

.commercial-nav .nav-link {
  color: var(--bs-body-color);
  border: 1px solid var(--bs-border-color);
}

.commercial-nav .router-link-active {
  color: var(--bs-light);
  background: var(--bs-dark);
  border-color: var(--bs-dark);
}
</style>
