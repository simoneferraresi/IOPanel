import vmbpy

vmb = vmbpy.VmbSystem.get_instance()
with vmb:
    cams = vmb.get_all_cameras()
    for cam in cams:
        print(cam)
