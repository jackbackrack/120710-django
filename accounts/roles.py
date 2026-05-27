def add_staff_role(user):
    if not user.is_staff:
        user.is_staff = True
        user.save(update_fields=['is_staff'])
